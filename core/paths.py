"""Cross-platform per-user data directory (caches, icons, fallback secrets)."""
import os
import sys
from pathlib import Path

APP_DIR_NAME = "DurangoSaveManager"


def get_data_dir() -> Path:
    override = os.environ.get("DURANGO_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "durango-save-manager"
    base.mkdir(parents=True, exist_ok=True)
    return base


DATA_DIR = get_data_dir()
