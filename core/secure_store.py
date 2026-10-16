"""
Secure token storage.

Uses the OS credential vault via `keyring`:
  - Windows: Credential Manager
  - Linux:   Secret Service (GNOME Keyring / KWallet)
  - macOS:   Keychain

Windows Credential Manager limits one secret to ~2.5 KB while our token bundle is
~8 KB, so the JSON is split into small chunks stored under numbered entries.

If no keyring backend is usable (e.g. headless Linux), falls back to a file in the
user data dir with owner-only permissions (0600).
"""
import json
import os
from typing import Optional

from .paths import DATA_DIR

SERVICE = "DurangoSaveManager"
COUNT_KEY = "tokens:chunks"
CHUNK_SIZE = 900  # chars; safe under Windows' 2560-byte (UTF-16) blob limit
FALLBACK_FILE = DATA_DIR / "tokens.json"

try:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
except Exception:  # keyring not installed
    keyring = None
    KeyringError = Exception
    PasswordDeleteError = Exception


def _keyring_usable() -> bool:
    if keyring is None:
        return False
    try:
        name = type(keyring.get_keyring()).__module__.lower()
        return "fail" not in name and "null" not in name
    except Exception:
        return False


def _delete_keyring_entries():
    if keyring is None:
        return
    try:
        count = int(keyring.get_password(SERVICE, COUNT_KEY) or 0)
    except Exception:
        count = 0
    for key in [COUNT_KEY] + [f"tokens:{i}" for i in range(count)]:
        try:
            keyring.delete_password(SERVICE, key)
        except (PasswordDeleteError, KeyringError):
            pass
        except Exception:
            pass


def save_secret(data: dict) -> bool:
    payload = json.dumps(data)
    if _keyring_usable():
        try:
            _delete_keyring_entries()
            chunks = [payload[i:i + CHUNK_SIZE] for i in range(0, len(payload), CHUNK_SIZE)]
            for i, chunk in enumerate(chunks):
                keyring.set_password(SERVICE, f"tokens:{i}", chunk)
            keyring.set_password(SERVICE, COUNT_KEY, str(len(chunks)))
            if FALLBACK_FILE.exists():
                try:
                    FALLBACK_FILE.unlink()
                except OSError:
                    pass
            return True
        except Exception as e:
            print(f"[Secure Store] Keyring write failed, using file fallback: {e}")
    # Fallback: owner-only file
    try:
        fd = os.open(str(FALLBACK_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        try:
            os.chmod(FALLBACK_FILE, 0o600)
        except OSError:
            pass
        return True
    except Exception as e:
        print(f"[Secure Store] Save failed: {e}")
        return False


def load_secret() -> Optional[dict]:
    if _keyring_usable():
        try:
            count = int(keyring.get_password(SERVICE, COUNT_KEY) or 0)
            if count > 0:
                parts = [keyring.get_password(SERVICE, f"tokens:{i}") for i in range(count)]
                if all(p is not None for p in parts):
                    return json.loads("".join(parts))
        except Exception as e:
            print(f"[Secure Store] Keyring read failed: {e}")
    if FALLBACK_FILE.is_file():
        try:
            with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Secure Store] Fallback read failed: {e}")
    return None


def delete_secret():
    _delete_keyring_entries()
    try:
        if FALLBACK_FILE.exists():
            FALLBACK_FILE.unlink()
    except OSError:
        pass
