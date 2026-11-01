import gi, sys
gi.require_version('Gtk','4.0')
gi.require_version('WebKit','6.0')
from gi.repository import Gtk, WebKit, GLib
def on_activate(app):
    win = Gtk.ApplicationWindow(application=app, title="test", default_width=1200, default_height=800)
    web = WebKit.WebView()
    win.set_child(web)
    def loaded(w, ev):
        if ev == WebKit.LoadEvent.FINISHED:
            print("LOAD FINISHED ok")
            def snap():
                try:
                    snap = web.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None)
                    snap.savev("/home/barunespadhy/Code/SoftwareDevelopment/deepseek-harness-linux/tests/dsh_snapshot.png", "png")
                    print("SNAPSHOT SAVED")
                except Exception as e:
                    print("snap err:", e)
                app.quit()
            GLib.timeout_add(6000, snap)
    web.connect("load-changed", loaded)
    web.load_uri("http://127.0.0.1:3080")
    win.present()
app = Gtk.Application()
app.connect("activate", on_activate)
sys.exit(app.run(None))
