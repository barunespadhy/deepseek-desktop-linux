import os, gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
pids = [int(p) for p in os.popen("pgrep -f '^python3 -m dsh_linux$'").read().split()]
print('wrapper pids:', pids)
for pid in pids:
    dest = f'org.kde.StatusNotifierItem-{pid}-1'
    try:
        v = conn.call_sync(dest, '/MenuBar', 'com.canonical.dbusmenu', 'GetLayout',
            GLib.Variant('(iias)', (0, -1, ['label'])), None, Gio.DBusCallFlags.NONE, 8000, None)
        print('GetLayout OK:', v.print_(True)[:300])
        print('unpacked:', v.unpack()[1][1])
    except Exception as e:
        print('GetLayout ERROR:', e)
