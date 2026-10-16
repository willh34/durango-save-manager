import sys
import threading
import time
import logging
import webview

# Suppress noisy disposal warnings on exit
logging.getLogger("pywebview").setLevel(logging.CRITICAL)

def run_login(url, redirect_prefix="ms-xal-000000004c12ae6f://auth"):
    captured_url = None
    
    def hook_webview():
        nonlocal captured_url
        # Attempt to hook WebView2's native NavigationStarting event for instant interception
        for _ in range(50):
            try:
                from webview.platforms.winforms import BrowserView
                instance = BrowserView.instances.get(win.uid)
                if instance and instance.browser and instance.browser.webview:
                    def on_nav(sender, args):
                        nonlocal captured_url
                        try:
                            uri = str(args.Uri)
                            if uri.startswith(redirect_prefix) or "oauth20_desktop.srf" in uri or uri.startswith("ms-xal-"):
                                args.Cancel = True
                                captured_url = uri
                                win.destroy()
                        except Exception:
                            pass
                    instance.browser.webview.NavigationStarting += on_nav
                    break
            except Exception:
                pass
            # Linux (GTK / WebKit2): intercept navigation to the custom-scheme redirect
            try:
                from webview.platforms.gtk import BrowserView as GtkBrowserView
                ginst = GtkBrowserView.instances.get(win.uid)
                if ginst and getattr(ginst, "webview", None):
                    def on_policy(wv, decision, decision_type):
                        nonlocal captured_url
                        try:
                            if decision_type == 0:  # NAVIGATION_ACTION
                                uri = decision.get_navigation_action().get_request().get_uri()
                                if uri.startswith(redirect_prefix) or uri.startswith("ms-xal-") or "oauth20_desktop.srf" in uri:
                                    captured_url = uri
                                    decision.ignore()
                                    win.destroy()
                                    return True
                        except Exception:
                            pass
                        return False
                    ginst.webview.connect("decide-policy", on_policy)
                    break
            except Exception:
                pass
            time.sleep(0.05)
            
        # Polling fallback in case navigation event is missed
        while not captured_url:
            time.sleep(0.1)
            try:
                curr = win.get_current_url()
                if curr and (curr.startswith(redirect_prefix) or "oauth20_desktop.srf" in curr or curr.startswith("ms-xal-")):
                    captured_url = curr
                    win.destroy()
                    break
            except Exception:
                break

    def on_shown():
        threading.Thread(target=hook_webview, daemon=True).start()

    win = webview.create_window(
        title="Sign in to Microsoft / Xbox Live",
        url=url,
        width=520,
        height=700,
        resizable=True,
        focus=True,
        background_color="#181818"
    )
    win.events.shown += on_shown
    
    # Private mode ensures cookies and credentials are isolated and forgotten upon closing
    webview.start(private_mode=True)
    return captured_url

if __name__ == "__main__":
    if len(sys.argv) > 1:
        login_url = sys.argv[1]
        pfx = sys.argv[2] if len(sys.argv) > 2 else "ms-xal-000000004c12ae6f://auth"
        redirect = run_login(login_url, pfx)
        if redirect:
            print("REDIRECT_URL:" + redirect)
        else:
            print("CANCELLED")
    else:
        print("Usage: webview_login.py <login_url> [redirect_prefix]")
