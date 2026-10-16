# Durango Save Manager

Download your Xbox saves on PC for use with translation layers like Azimuth and WinDurango.

## Requirements

- Python 3.10+
- **Windows:** [WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/) (preinstalled on Windows 11)
- **Linux:** GTK or Qt WebKit for pywebview, and a Secret Service keyring (GNOME Keyring / KWallet):
  - Debian/Ubuntu: `sudo apt install python3-gi gir1.2-webkit2-4.1 gnome-keyring`
  - or install the Qt backend: `pip install "pywebview[qt]"`

## Run

```
pip install -r requirements.txt
python app.py
```

## Notes
- The project is not affiliated with Microsoft or Xbox and may be against TOS. Use at your own risk.
- Thanks to xbox-webapi-python and Xodus for code references.
