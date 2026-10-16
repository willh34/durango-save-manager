import os
import re
import asyncio
import shutil
import tempfile

import httpx
from urllib.parse import quote

from .async_utils import run_async
from .titlehub import get_default_scid

# Xbox Title Storage handles parallel requests well; these keep us fast without hammering it.
MAX_CONCURRENCY = 12
_BASE_URL = "https://titlestorage.xboxlive.com"


class DownloadCancelled(Exception):
    """Raised when the user cancels an in-progress download."""


def safe_component(name, fallback="unnamed"):
    """Makes a server-supplied name safe to use as a single path component."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip().rstrip(".")
    if s in ("", ".", ".."):
        return fallback
    return s[:180]


def _http2_available():
    try:
        import h2  # noqa: F401
        return True
    except Exception:
        return False


async def download_connected_storage_direct(client, title_id, pfn, scid, work_dir, progress_cb=None, cancel_event=None):
    """
    Downloads every connected-storage container for a title into `work_dir`, in parallel.
    Returns a manifest: [{"name": container, "blobs": [{"name": blob, "path": file_path}]}]
    """
    if not scid:
        scid = get_default_scid(title_id)
    scid = str(scid)
    if not re.fullmatch(r"[0-9A-Fa-f\-]{8,40}", scid):
        raise RuntimeError(f"Invalid SCID: {scid}")
    xuid = client.xuid

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()

    limits = httpx.Limits(max_connections=MAX_CONCURRENCY * 2, max_keepalive_connections=MAX_CONCURRENCY * 2)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    refresh_lock = asyncio.Lock()

    async with httpx.AsyncClient(timeout=60.0, limits=limits, http2=_http2_available()) as http_client:

        def build_headers():
            return {
                "Authorization": client.auth_header,
                "x-xbl-contract-version": "2",
                "x-xbl-pfn": pfn or "",
                "User-Agent": "XboxApp/39.39.22001.0",
            }

        async def request(url, params=None, stream_to=None):
            """GET with cancel check, concurrency limit, token refresh on 401, and retry on 429/5xx."""
            check_cancel()
            async with sem:
                refreshed = False
                for attempt in range(5):
                    check_cancel()
                    gen = client.generation
                    try:
                        async with http_client.stream("GET", url, headers=build_headers(), params=params) as resp:
                            if resp.status_code == 401 and not refreshed:
                                refreshed = True
                                async with refresh_lock:
                                    if client.generation == gen:
                                        await client.refresh()
                                continue
                            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                                try:
                                    wait = float(resp.headers.get("Retry-After", 1.5 * (attempt + 1)))
                                except ValueError:
                                    wait = 1.5 * (attempt + 1)
                                await asyncio.sleep(min(wait, 6.0))
                                continue
                            if resp.status_code == 404:
                                return None
                            resp.raise_for_status()
                            if stream_to:
                                with open(stream_to, "wb") as f:
                                    async for chunk in resp.aiter_bytes(65536):
                                        f.write(chunk)
                                return stream_to
                            await resp.aread()
                            return resp.json()
                    except (httpx.TransportError, httpx.TimeoutException):
                        if attempt >= 4:
                            raise
                        await asyncio.sleep(1.0 * (attempt + 1))
                raise RuntimeError("Request failed after retries.")

        if progress_cb:
            progress_cb("Querying Xbox Title Storage container list...", 5)

        # 1. Fetch container list (paged; sequential by nature)
        containers_blobs = []
        continuation_token = None
        while True:
            params = {"continuationToken": continuation_token} if continuation_token else {}
            data = await request(f"{_BASE_URL}/connectedstorage/users/xuid({xuid})/scids/{scid}", params=params)
            if data is None:
                break
            containers_blobs.extend(data.get("blobs", []))
            continuation_token = data.get("pagingInfo", {}).get("continuationToken")
            if not continuation_token:
                break

        valid = [b for b in containers_blobs if b.get("fileName", "").endswith(",savedgame")]
        if not valid:
            raise RuntimeError(f"No cloud save containers found for title {title_id} (SCID: {scid}). Make sure you have played this game on Xbox One/Series.")

        # 2. Fetch every container's atom list in parallel
        names = [b["fileName"][:-len(",savedgame")] for b in valid]
        if progress_cb:
            progress_cb(f"Reading {len(names)} save container(s)...", 10)

        async def get_atoms(name):
            d = await request(f"{_BASE_URL}/connectedstorage/users/xuid({xuid})/scids/{scid}/savedgames/{quote(name, safe='')}")
            return (d or {}).get("atoms", [])

        # 3. Download every atom in parallel, streaming straight to disk
        done = 0
        total = 0
        manifest = []

        async def run_all():
            nonlocal done, total
            atom_lists = await asyncio.gather(*[get_atoms(n) for n in names])
            total = sum(len(a) for a in atom_lists) or 1
            jobs = []
            used_names = {}

            async def fetch_atom(atom_id, dest):
                nonlocal done
                await request(f"{_BASE_URL}/connectedstorage/users/xuid({xuid})/scids/{scid}/{atom_id},binary", stream_to=dest)
                done += 1
                if progress_cb:
                    progress_cb(f"Downloading save data ({done}/{total})", int(12 + (done / total) * 80))

            for ci, (cname, atoms) in enumerate(zip(names, atom_lists)):
                entry = {"name": cname, "blobs": []}
                manifest.append(entry)
                seen = used_names.setdefault(ci, set())
                for ai, atom in enumerate(atoms):
                    blob_name = safe_component(atom.get("name"), fallback=f"blob_{ai}")
                    while blob_name.lower() in seen:
                        blob_name = f"{blob_name}_{ai}"
                    seen.add(blob_name.lower())
                    dest = os.path.join(work_dir, f"{ci}_{ai}.part")
                    entry["blobs"].append({"name": blob_name, "path": dest})
                    jobs.append(fetch_atom(atom.get("atom"), dest))
            await asyncio.gather(*jobs)

        main_task = asyncio.ensure_future(run_all())

        async def watch_cancel():
            while not main_task.done():
                if cancel_event is not None and cancel_event.is_set():
                    main_task.cancel()
                    return
                await asyncio.sleep(0.15)

        watcher = asyncio.ensure_future(watch_cancel())
        try:
            await main_task
        except asyncio.CancelledError:
            raise DownloadCancelled()
        finally:
            watcher.cancel()

        if progress_cb:
            progress_cb("Finalizing save download...", 95)
        return manifest


def download_save(title_id, pfn, scid, work_dir, client=None, progress_cb=None, cancel_event=None):
    """
    Downloads connected storage for a title into work_dir (parallel).
    Returns (success, manifest_or_None, error_message).
    """
    if not client or not getattr(client, "auth_header", None):
        return False, None, "Xbox Live client is not authenticated."

    try:
        manifest = run_async(
            download_connected_storage_direct(client, title_id, pfn, scid, work_dir, progress_cb=progress_cb, cancel_event=cancel_event)
        )
        return True, manifest, ""
    except DownloadCancelled:
        return False, None, "__cancelled__"
    except Exception as e:
        print(f"[Direct Download Error] {e}")
        return False, None, str(e) or e.__class__.__name__


def make_work_dir(parent):
    """Temp directory next to the output so the final move is a cheap rename on the same drive."""
    os.makedirs(parent, exist_ok=True)
    return tempfile.mkdtemp(prefix=".durango_tmp_", dir=parent)


def cleanup_dir(path):
    shutil.rmtree(path, ignore_errors=True)
