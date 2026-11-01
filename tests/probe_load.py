"""Probe how WebKitGTK loads the dsh web page (used by the test harness)."""
import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, WebKit, GLib

token = sys.argv[1] if len(sys.argv) > 1 else ''
url = f'http://127.0.0.1:3095/?token={token}'
app = Gtk.Application()


def on_activate(a):
    win = Gtk.ApplicationWindow(application=a)
    web = WebKit.WebView()
    win.set_child(web)
    finished = {'value': False}

    def changed(w, e):
        uri = w.get_uri() or 'none'
        print(f'load-changed event {int(e)}: {uri[:90]}', flush=True)
        if int(e) == 3 and not finished['value']:
            finished['value'] = True
            print('FINISHED reached', flush=True)

            def probe():
                def got(_wv, res, _u):
                    try:
                        print('JS:', res.get_js_value().to_string()[:150], flush=True)
                    except Exception as ex:
                        print('JS ERR:', ex, flush=True)
                    a.quit()
                web.evaluate_javascript(
                    "document.readyState + '|' + document.title + '|' + String(document.body ? document.body.childElementCount : -1)",
                    -1, None, None, got, None)
            GLib.timeout_add(4000, probe)
            GLib.timeout_add(25000, lambda: (print('probe timeout, quitting', flush=True), a.quit()))

    def failed(w, e, uri, err):
        print(f'load-failed: {uri} {err}', flush=True)

    web.connect('load-changed', changed)
    web.connect('load-failed', failed)
    web.load_uri(url)
    win.present()


app.connect('activate', on_activate)
app.run(None)
