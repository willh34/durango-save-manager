import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import bottle
import httpx
import webview
from webview.http import BottleServer

# Ensure local directories and submodules are in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.auth import XALAuthContext, load_cached_tokens, clear_cached_tokens
from core.async_utils import run_async
from core.azimuth import export_manifest
from core.client import XALLiveClient
from core.icons import IconCacheManager, compress_image
from core.paths import DATA_DIR
from core.storage import download_save, make_work_dir, cleanup_dir, safe_component
from core.titlehub import fetch_user_console_games
from core.webview_auth import spawn_login_window

# Caches live in a per-user writable directory (works for read-only installs / frozen builds)
GAMES_CACHE_FILE = DATA_DIR / "games_cache.json"
GAMERPIC_FILE = DATA_DIR / "gamerpic.jpg"
ICONS_DIR = DATA_DIR / "icons"
UI_DIR = BASE_DIR / "ui"

DEFAULT_PLAYER_ID = "User_9000000000000"
PLAYER_ID_RE = re.compile(r"User_\d{1,20}")


class DurangoServer(BottleServer):
    """
    Custom HTTP server for PyWebView.
    Serves only the UI assets, cached icons and the gamerpic - never the rest of the app folder.
    """
    @classmethod
    def start_server(cls, urls, http_port, keyfile=None, certfile=None):
        server = cls()
        server.root_path = str(BASE_DIR)
        app = bottle.Bottle()

        @app.post(f"/js_api/{server.uid}")
        def js_api():
            bottle.response.headers["Access-Control-Allow-Origin"] = "*"
            bottle.response.headers["Access-Control-Allow-Methods"] = "PUT, GET, POST, DELETE, OPTIONS"
            bottle.response.headers["Access-Control-Allow-Headers"] = "Origin, Accept, Content-Type, X-Requested-With, X-CSRF-Token"
            body = json.loads(bottle.request.body.read().decode("utf-8"))
            if body["uid"] in server.js_callback:
                return json.dumps(server.js_callback[body["uid"]](body))

        @app.route("/")
        @app.route("/index.html")
        def index():
            return bottle.static_file("index.html", root=str(UI_DIR))

        @app.route("/icons/<file:path>")
        def icons(file):
            return bottle.static_file(file, root=str(ICONS_DIR))

        @app.route("/gamerpic.jpg")
        def gamerpic():
            return bottle.static_file("gamerpic.jpg", root=str(DATA_DIR))

        @app.route("/ui/<file:path>")
        def ui_dir(file):
            return bottle.static_file(file, root=str(UI_DIR))

        @app.route("/<file:path>")
        def ui_files(file):
            if (UI_DIR / file).is_file():
                return bottle.static_file(file, root=str(UI_DIR))
            return bottle.static_file(file, root=str(ICONS_DIR))

        server.port = http_port or webview.http._get_random_port()
        server.thread = threading.Thread(
            target=lambda: bottle.run(app=app, server=webview.http.ThreadedAdapter, port=server.port, quiet=True),
            daemon=True
        )
        server.thread.start()
        server.running = True
        server.address = f"http://127.0.0.1:{server.port}/"
        cls.common_path = str(BASE_DIR)
        server.js_api_endpoint = f"{server.address}js_api/{server.uid}"
        return server.address, cls.common_path, server


def load_cached_games():
    try:
        with open(GAMES_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[Games Cache] Warning: {e}")
        return []


def save_cached_games(games):
    """Atomic write so a crash mid-save can't corrupt the cache."""
    tmp = GAMES_CACHE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(games, f)
        os.replace(tmp, GAMES_CACHE_FILE)
    except Exception as e:
        print(f"[Games Cache] Save warning: {e}")


def remove_files(*paths):
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass


def spawn(fn):
    threading.Thread(target=fn, daemon=True).start()


class DurangoBridge:
    """
    Bridge interface exposed to JavaScript via window.pywebview.api.
    CRITICAL: All internal instance attributes AND helper methods MUST start with an
    underscore ('_') so that pywebview's JS API reflection does not expose them or
    recursively traverse internal Python/.NET objects (which freezes the app).
    """
    def __init__(self):
        self._window = None
        self._client = None
        self._cancel_event = threading.Event()
        self._login_active = threading.Lock()
        self._js_queue = queue.Queue()
        self._icons = IconCacheManager(ICONS_DIR)
        self._icons.set_bridge(self)
        spawn(self._js_dispatcher)

    # ---- internal helpers -------------------------------------------------

    def _set_window(self, window):
        self._window = window

    def _js_dispatcher(self):
        """Single ordered consumer so UI callbacks arrive in order without a thread per call."""
        while True:
            script = self._js_queue.get()
            if not self._window:
                continue
            try:
                self._window.evaluate_js(script)
            except Exception as e:
                print(f"[Bridge JS Warning] {e}")

    def _call_js(self, fn, *args):
        """Queues `app.<fn>(<json args>)` for execution in the page."""
        self._js_queue.put(f"app.{fn}({', '.join(json.dumps(a) for a in args)})")

    def _attach_local_icons(self, games):
        for g in games:
            tid_hex = g.get("title_id_hex")
            local = self._icons.get_local_path(tid_hex) if tid_hex else None
            if local:
                g["local_icon"] = local

    def _clear_local_data(self):
        remove_files(GAMES_CACHE_FILE, GAMERPIC_FILE)
        self._icons.clear()

    # ---- JS-facing API ----------------------------------------------------

    def get_initial_state(self):
        """
        MUST return instantly from disk without ANY network calls.
        Returns cached games & gamerpic so library renders on frame 1.
        """
        try:
            cached_auth = load_cached_tokens()
            if cached_auth:
                self._client = XALLiveClient(cached_auth)
                games = load_cached_games()
                self._attach_local_icons(games)
                pic = ""
                if GAMERPIC_FILE.is_file() and GAMERPIC_FILE.stat().st_size > 0:
                    pic = f"/gamerpic.jpg?t={int(GAMERPIC_FILE.stat().st_mtime)}"
                return {
                    "signed_in": True,
                    "gamertag": self._client.gamertag,
                    "xuid": self._client.xuid,
                    "cached_games": games,
                    "cached_gamerpic": pic
                }
        except Exception as e:
            print(f"[Initial State] Cache load warning: {e}")

        return {
            "signed_in": False,
            "gamertag": "Not Signed In",
            "xuid": "",
            "cached_games": [],
            "cached_gamerpic": ""
        }

    def request_icon_cache(self, title_id_hex, remote_url):
        """Dispatches an on-demand background fetch for an icon used on screen."""
        if title_id_hex and remote_url:
            self._icons.queue_download(title_id_hex, remote_url)
        return True

    def request_profile(self):
        """Fetches the user's gamerpic in a background thread and saves it to gamerpic.jpg."""
        client = self._client
        if not client:
            return False

        def worker():
            try:
                gamerpic_url = run_async(client.get_profile_gamerpic())
                if not gamerpic_url:
                    return
                resp = httpx.get(gamerpic_url, timeout=12.0, follow_redirects=True)
                if resp.status_code == 200 and resp.content:
                    GAMERPIC_FILE.write_bytes(compress_image(resp.content, max_size=(160, 160), quality=85))
                self._call_js("onGamerpicLoaded", f"/gamerpic.jpg?t={int(time.time())}")
            except Exception as e:
                print(f"[Profile Worker Error] {e}")

        spawn(worker)
        return True

    def request_games(self):
        """Fetches the user's Xbox games in a background thread and reuses image cache."""
        client = self._client
        if not client:
            return False

        def worker():
            try:
                self._call_js("onProgressUpdate", 15)
                games = run_async(fetch_user_console_games(
                    client,
                    max_items=250,
                    existing_cache=load_cached_games(),
                    progress_callback=lambda pct: self._call_js("onProgressUpdate", pct)
                ))
                self._attach_local_icons(games)
                save_cached_games(games)
                self._call_js("onGamesLoaded", games)
            except Exception as e:
                print(f"[Games Worker Error] {e}")
                self._call_js("onGamesError", str(e))

        spawn(worker)
        return True

    def refresh_library(self):
        """Re-queries Titlehub and Achievements to update library data."""
        if self._client:
            self.request_profile()
            self.request_games()
        return True

    def clear_cache_and_refresh(self):
        """Wipes games, gamerpic and icon caches, then fetches everything fresh."""
        self._clear_local_data()
        return self.refresh_library()

    def start_login(self):
        """
        Spawns the login process on a dedicated background thread.
        Returns immediately so pywebview stays responsive; ignores repeat clicks while a login is open.
        """
        if not self._login_active.acquire(blocking=False):
            return False

        def worker():
            try:
                auth_ctx = XALAuthContext()
                login_url = run_async(auth_ctx.prepare_login())

                # Isolated login dialog in a child process
                redirect_url = spawn_login_window(login_url, redirect_prefix=auth_ctx.redirect_uri)
                if not redirect_url:
                    self._call_js("onLoginCancelled")
                    return

                self._client = XALLiveClient(run_async(auth_ctx.complete_login(redirect_url)))
                self._call_js("onLoginSuccess", {
                    "gamertag": self._client.gamertag,
                    "xuid": self._client.xuid
                })
                self.request_profile()
                self.request_games()
            except Exception as e:
                print(f"[Login Error] {e}")
                self._call_js("onLoginFailed", str(e))
            finally:
                self._login_active.release()

        spawn(worker)
        return True

    def sign_out(self):
        """Clears active session, saved tokens, and local caches."""
        self._client = None
        clear_cached_tokens()
        self._clear_local_data()
        return True

    def get_default_download_dir(self):
        """Returns the user's Downloads folder (or home if it doesn't exist)."""
        downloads = Path.home() / "Downloads"
        return str(downloads if downloads.is_dir() else Path.home())

    def select_folder(self, default_dir=""):
        """Displays the native folder picker."""
        if not self._window:
            return ""
        # Modern FileDialog.FOLDER with fallback to the legacy constant
        dialog_type = getattr(getattr(webview, "FileDialog", None), "FOLDER", getattr(webview, "FOLDER_DIALOG", 20))
        result = self._window.create_file_dialog(dialog_type, directory=default_dir or "")
        return result[0] if result else ""

    def cancel_download(self):
        """Signals the active download worker to stop at the next safe point."""
        self._cancel_event.set()
        return True

    def download_save(self, params):
        """
        Executes Connected Storage save download in a background thread.
        Streams progress updates back to the UI.
        """
        output_dir = params.get("output_dir")
        try:
            title_id = int(params.get("title_id"))
        except (TypeError, ValueError):
            title_id = 0
        if not output_dir or title_id <= 0:
            return {"success": False, "error": "Invalid output directory or Title ID."}
        if not self._client:
            return {"success": False, "error": "Not signed in."}

        scid = params.get("scid")
        pfn = params.get("pfn")
        export_azimuth = bool(params.get("export_azimuth", False))
        player_id = params.get("player_id") or DEFAULT_PLAYER_ID
        if not PLAYER_ID_RE.fullmatch(player_id):
            player_id = DEFAULT_PLAYER_ID

        # Selected destination is top-level (e.g. Downloads); create a per-game subfolder inside it
        game_name = params.get("game_name") or f"Title_{title_id}"
        safe_name = re.sub(r"\s+", " ", safe_component(game_name, f"Title_{title_id}"))
        game_folder = os.path.join(output_dir, safe_name)
        folder_existed = os.path.isdir(game_folder)
        try:
            os.makedirs(game_folder, exist_ok=True)
        except OSError as e:
            return {"success": False, "error": f"Cannot create output folder: {e}"}

        client = self._client
        cancel_event = threading.Event()
        self._cancel_event = cancel_event

        def worker():
            def progress(status_text, percent):
                self._call_js("onDownloadProgress", status_text, percent)

            def report(result):
                self._call_js("onDownloadResult", result)

            work_dir = None
            try:
                work_dir = make_work_dir(game_folder)
                success, manifest, err = download_save(
                    title_id=title_id, pfn=pfn, scid=scid, work_dir=work_dir,
                    client=client, progress_cb=progress, cancel_event=cancel_event
                )
                if not success:
                    if err == "__cancelled__" or cancel_event.is_set():
                        report({"success": False, "cancelled": True})
                    else:
                        report({"success": False, "error": err.strip() or "Failed to download connected storage."})
                    return

                progress("Generating Azimuth revisions..." if export_azimuth else "Writing save files...", 96)
                extracted = export_manifest(manifest, game_folder, export_azimuth, player_id)
                progress("Complete!", 100)
                report({"success": True, "container_count": len(extracted), "folder": game_folder})
            except Exception as e:
                print(f"[Download Error] {e}")
                report({"success": False, "error": str(e) or e.__class__.__name__})
            finally:
                if work_dir:
                    cleanup_dir(work_dir)
                # Don't leave an empty folder behind after a failed/cancelled download
                if not folder_existed:
                    try:
                        os.rmdir(game_folder)
                    except OSError:
                        pass

        spawn(worker)
        return True

    def open_folder(self, folder_path):
        """Opens a folder in the platform file manager."""
        if not (folder_path and os.path.isdir(folder_path)):
            return False
        try:
            if sys.platform == "win32":
                os.startfile(folder_path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder_path])
            return True
        except Exception as e:
            print(f"[Open Folder Warning] {e}")
            return False


def main():
    bridge = DurangoBridge()
    window = webview.create_window(
        title="Durango Save Tool",
        url=str(UI_DIR / "index.html"),
        js_api=bridge,
        width=1080,
        height=720,
        min_size=(800, 560),
        background_color="#181818",
        focus=True
    )
    bridge._set_window(window)

    # Start WebView application (Edge Chromium WebView2 on Windows) with DurangoServer
    webview.start(server=DurangoServer, debug=False)


if __name__ == "__main__":
    main()
