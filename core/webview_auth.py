import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def spawn_login_window(login_url, redirect_prefix="ms-xal-000000004c12ae6f://auth"):
    """
    Launches an isolated pywebview login window in a subprocess to handle OAuth login
    and redirect interception with clean private mode.
    """
    script_path = BASE_DIR / "webview_login.py"
    cmd = [sys.executable, str(script_path), login_url, redirect_prefix]
    
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = proc.communicate()
    
    for line in stdout.splitlines():
        if line.startswith("REDIRECT_URL:"):
            return line[len("REDIRECT_URL:"):].strip()
        if "CANCELLED" in line:
            return None
            
    return None
