import io
import queue
import threading
from pathlib import Path

import httpx
from PIL import Image


def compress_image(raw_bytes: bytes, max_size=(260, 390), quality=75) -> bytes:
    """
    Resizes and compresses images to thumbnail proportions using Pillow.
    Drastically reduces disk space (~20KB vs multi-megabyte raw downloads)
    and guarantees instant rendering without UI stutter.
    """
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            # Handle alpha transparency / palette modes
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                rgb_img = Image.new("RGB", img.size, (28, 28, 28))
                rgb_img.paste(img, mask=img.getchannel("A"))
                img = rgb_img
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Fast bilinear downsampling for speed and low CPU usage
            img.thumbnail(max_size, Image.Resampling.BILINEAR)

            out_buf = io.BytesIO()
            img.save(out_buf, format="JPEG", quality=quality, optimize=True)
            return out_buf.getvalue()
    except Exception as e:
        print(f"[Icon Cache] Compression failed, saving raw: {e}")
        return raw_bytes


class IconCacheManager:
    """
    Manages local game icon file caching and fast thumbnail compression.
    Downloads only icons that are actually requested/displayed by the UI,
    persisting lightweight compressed thumbnails to the local 'icons/' folder.
    Operates via a multi-threaded worker pool with HTTP connection pooling for speed.
    """
    def __init__(self, icons_dir: Path, num_workers: int = 4):
        self.icons_dir = Path(icons_dir)
        self.icons_dir.mkdir(parents=True, exist_ok=True)
        self._queue = queue.Queue()
        self._queued_set = set()
        self._lock = threading.Lock()
        self._bridge = None
        self._client = httpx.Client(
            timeout=10.0,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16)
        )
        for _ in range(num_workers):
            threading.Thread(target=self._worker, daemon=True).start()

    def set_bridge(self, bridge):
        self._bridge = bridge

    @staticmethod
    def _clean_tid(title_id_hex: str) -> str:
        s = str(title_id_hex).strip().upper()
        return s[2:] if s.startswith("0X") else s

    def _icon_path(self, safe_tid: str) -> Path:
        return self.icons_dir / f"0x{safe_tid}.jpg"

    def _is_cached(self, safe_tid: str) -> bool:
        target = self._icon_path(safe_tid)
        return target.is_file() and target.stat().st_size > 0

    def get_local_path(self, title_id_hex: str):
        """Returns URL '/icons/0x{TID}.jpg' if the icon file exists locally."""
        if not title_id_hex:
            return None
        safe_tid = self._clean_tid(title_id_hex)
        return f"/icons/0x{safe_tid}.jpg" if self._is_cached(safe_tid) else None

    def queue_download(self, title_id_hex: str, remote_url: str):
        """Enqueues an on-demand download and thumbnail compression for an icon."""
        if not title_id_hex or not str(remote_url or "").startswith("http"):
            return
        safe_tid = self._clean_tid(title_id_hex)

        with self._lock:
            # Skip if already downloaded or currently in queue
            if safe_tid in self._queued_set or self._is_cached(safe_tid):
                return
            self._queued_set.add(safe_tid)
            self._queue.put((safe_tid, remote_url))

    def _worker(self):
        while True:
            safe_tid, remote_url = self._queue.get()
            try:
                if not self._is_cached(safe_tid):
                    resp = self._client.get(remote_url)
                    if resp.status_code == 200 and resp.content:
                        self._icon_path(safe_tid).write_bytes(compress_image(resp.content))
                        if self._bridge:
                            self._bridge._call_js("onIconCached", f"0x{safe_tid}", f"/icons/0x{safe_tid}.jpg")
            except Exception as e:
                print(f"[Icon Cache] Error caching 0x{safe_tid}: {e}")

    def clear(self):
        """Deletes all cached icon files and resets download queue state."""
        with self._lock:
            self._queued_set.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        for f in self.icons_dir.glob("*.jpg"):
            try:
                f.unlink()
            except OSError:
                pass
