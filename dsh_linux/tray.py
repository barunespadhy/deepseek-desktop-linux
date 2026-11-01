"""StatusNotifierItem (appindicator) tray icon over the session D-Bus.

Implements the freedesktop SNI protocol directly with Gio so it works on
KDE Plasma (native SNI), GNOME (with the AppIndicator extension), COSMIC,
XFCE, MATE, Cinnamon, and Budgie without extra Python dependencies. The
right-click menu is a minimal com.canonical.dbusmenu tree with Show and Quit.
"""

import os
import threading

import gi

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Gio, GLib

from .icons_data import ICONS

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="Scroll"><arg type="i" name="delta" direction="in"/><arg type="s" name="orientation" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s" name="status" direction="out"/></signal>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
  </interface>
</node>
"""

WATCHER_BUS = 'org.kde.StatusNotifierWatcher'
WATCHER_PATH = '/StatusNotifierWatcher'


def pixbuf_from_png(png: bytes):
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(png))
    return GdkPixbuf.Pixbuf.new_from_stream(stream, None)


def pixbuf_argb_entry(pixbuf):
    """Serialize a pixbuf as one SNI `(iiay)` ARGB32 pixmap tuple."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    rowstride = pixbuf.get_rowstride()
    pixels = pixbuf.get_pixels()
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            offset = y * rowstride + x * 4
            r, g, b, a = pixels[offset], pixels[offset + 1], pixels[offset + 2], pixels[offset + 3]
            row += bytes((a, r, g, b))
        rows.append(bytes(row))
    return (width, height, GLib.Variant('ay', b''.join(rows)))


class StatusNotifierItem:
    """One SNI item plus its dbusmenu, hosted on the session bus."""

    def __init__(self, on_activate, on_quit, on_secondary=None, on_update=None):
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if self.conn is None:
            raise RuntimeError('no session D-Bus available')
        self.on_activate = on_activate
        self.on_secondary = on_secondary or on_activate
        self.on_quit = on_quit
        self.on_update = on_update or on_activate
        self.update_version = None  # set while an update is available
        self.service = f'org.kde.StatusNotifierItem-{os.getpid()}-1'
        self.item_path = '/StatusNotifierItem'
        self.menu_path = '/MenuBar'
        self.icon_style = 'coloured'
        self.status = 'Active'  # 'Active' shows the icon, 'Passive' hides it
        self._item_node_info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML)
        self._menu_node_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML)
        self._owner_id = 0
        self._item_reg_id = 0
        self._menu_reg_id = 0
        self._watcher_sub_id = 0
        self._register()

    # -- D-Bus plumbing -------------------------------------------------------

    def _register(self):
        self._owner_id = Gio.bus_own_name_on_connection(
            self.conn, self.service, Gio.BusNameOwnerFlags.NONE, None, None)
        item_iface = self._item_node_info.interfaces[0]
        self._item_reg_id = self.conn.register_object(
            self.item_path, item_iface, self._handle_item_call, self._item_get_prop, self._item_set_prop)
        menu_iface = self._menu_node_info.interfaces[0]
        self._menu_reg_id = self.conn.register_object(
            self.menu_path, menu_iface, self._handle_menu_call, self._menu_get_prop, self._menu_set_prop)
        self._watcher_sub_id = self.conn.signal_subscribe(
            'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE,
            self._on_owner_changed, None)
        self._try_watcher_register()

    def _try_watcher_register(self):
        def done(_connection, result, _user_data):
            try:
                self.conn.call_finish(result)
            except GLib.Error:
                pass  # watcher may be mid-restart; NameOwnerChanged re-arms us
        self.conn.call(
            WATCHER_BUS, WATCHER_PATH, WATCHER_BUS, 'RegisterStatusNotifierItem',
            GLib.Variant('(s)', (self.service,)), None,
            Gio.DBusCallFlags.NONE, -1, None, done, None)

    def _on_owner_changed(self, _conn, _sender, _path, _iface, _signal, parameters, _data):
        name, _old, new = parameters.unpack()
        if name == WATCHER_BUS and new:
            self._try_watcher_register()

    # -- SNI methods and properties --------------------------------------------

    def _item_get_prop(self, *args):
        name = args[-1]
        props = {
            'Category': GLib.Variant('s', 'ApplicationStatus'),
            'Id': GLib.Variant('s', 'dsh-linux'),
            'Title': GLib.Variant('s', 'DeepSeek Harness'),
            'Status': GLib.Variant('s', self.status),
            'WindowId': GLib.Variant('i', 0),
            'IconName': GLib.Variant('s', self.icon_name()),
            'IconPixmap': self.icon_pixmap(),
            'OverlayIconName': GLib.Variant('s', ''),
            'OverlayIconPixmap': GLib.Variant('a(iiay)', []),
            'AttentionIconName': GLib.Variant('s', ''),
            'AttentionIconPixmap': GLib.Variant('a(iiay)', []),
            'AttentionMovieName': GLib.Variant('s', ''),
            'ToolTip': GLib.Variant('(sa(iiay)ss)', ('', [], 'DeepSeek Harness', '')),
            'ItemIsMenu': GLib.Variant('b', False),
            'Menu': GLib.Variant('o', self.menu_path),
        }
        return props.get(name)

    def _item_set_prop(self, _iface, _name, _value):
        return False

    def _handle_item_call(self, _conn, _sender, _path, _iface, method_name, parameters, invocation):
        if method_name in ('Activate', 'SecondaryActivate'):
            x, y = parameters.unpack()
            handler = self.on_activate if method_name == 'Activate' else self.on_secondary
            GLib.idle_add(handler, x, y)
        invocation.return_value(None)
        return None

    def icon_name(self):
        base = 'deepseek-harness'
        return f'{base}-symbolic' if self.icon_style == 'symbolic' else base

    def icon_pixmap(self):
        # Offer every rendered size so the panel picks the one matching its
        # own (including 2x/3x HiDPI panels) instead of upscaling a single
        # 24px image into a blurry blob.
        prefix = f'{self.icon_style}-'
        entries = []
        for key in sorted(ICONS):
            if not key.startswith(prefix):
                continue
            try:
                entries.append(pixbuf_argb_entry(pixbuf_from_png(ICONS[key])))
            except Exception:
                # A broken image loader must not break property reads; panels
                # fall back to the theme icon name for the missing sizes.
                continue
        try:
            return GLib.Variant('a(iiay)', entries)
        except Exception:
            return GLib.Variant('a(iiay)', [])

    # -- state changes -----------------------------------------------------------

    def set_icon_style(self, style: str):
        if style not in ('coloured', 'symbolic'):
            return
        self.icon_style = style
        self._emit('NewIcon', None)
        self._emit('NewToolTip', None)

    def set_visible(self, visible: bool):
        status = 'Active' if visible else 'Passive'
        if status == self.status:
            return
        self.status = status
        self._emit('NewStatus', GLib.Variant('(s)', (status,)))

    def _emit(self, signal_name, parameters):
        self.conn.emit_signal(None, self.item_path, 'org.kde.StatusNotifierItem', signal_name, parameters)

    # -- dbusmenu ---------------------------------------------------------------

    def _menu_layout(self):
        """Build the `(u(ia{sv}av))` layout (GI-safe sequential builder)."""
        def node(menu_id, label):
            return GLib.Variant('(ia{sv}av)', (
                menu_id,
                {
                    'label': GLib.Variant('s', label),
                    'enabled': GLib.Variant('b', True),
                    'visible': GLib.Variant('b', True),
                    'type': GLib.Variant('s', 'standard'),
                },
                [],
            ))
        children = [node(1, 'Show DeepSeek Harness')]
        if self.update_version:
            children.append(node(3, f'Update available: {self.update_version}'))
        children.append(node(2, 'Quit'))
        root = GLib.Variant('(ia{sv}av)', (
            0,
            {
                'label': GLib.Variant('s', ''),
                'children-display': GLib.Variant('s', 'submenu'),
            },
            children,
        ))
        builder = GLib.VariantBuilder.new(GLib.VariantType.new('(u(ia{sv}av))'))
        builder.add_value(GLib.Variant('u', 0))
        builder.add_value(root)
        return builder.end()

    def set_update_available(self, version):
        """Show or hide the 'Update available' context menu entry."""
        version = str(version) if version else None
        if version == self.update_version:
            return
        self.update_version = version
        # Tell panels to re-fetch the layout.
        self.conn.emit_signal(
            None, self.menu_path, 'com.canonical.dbusmenu', 'LayoutUpdated',
            GLib.Variant('(uu)', (0, 0)))

    def _menu_get_prop(self, *args):
        name = args[-1]
        props = {
            'Version': GLib.Variant('u', 0),
            'TextDirection': GLib.Variant('s', 'ltr'),
            'Status': GLib.Variant('s', 'normal'),
            'IconThemePath': GLib.Variant('as', []),
        }
        return props.get(name)

    def _menu_set_prop(self, _iface, _name, _value):
        return False

    def _handle_menu_call(self, _conn, _sender, _path, _iface, method_name, parameters, invocation):
        if method_name == 'GetLayout':
            _parent, _depth, _props = parameters.unpack()
            invocation.return_value(self._menu_layout())
            return None
        if method_name == 'GetGroupProperties':
            _ids, _props = parameters.unpack()
            invocation.return_value(GLib.Variant('(a(ia{sv}))', ([],)))
            return None
        if method_name == 'GetProperty':
            menu_id, name = parameters.unpack()
            if name == 'label':
                if menu_id == 1:
                    label = 'Show DeepSeek Harness'
                elif menu_id == 3:
                    label = f'Update available: {self.update_version}'
                else:
                    label = 'Quit'
                invocation.return_value(GLib.Variant('(v)', (GLib.Variant('s', label),)))
                return None
            if name in ('enabled', 'visible'):
                invocation.return_value(GLib.Variant('(v)', (GLib.Variant('b', True),)))
                return None
            if name == 'type':
                invocation.return_value(GLib.Variant('(v)', (GLib.Variant('s', 'standard'),)))
                return None
            invocation.return_value(GLib.Variant('(v)', (GLib.Variant('s', ''),)))
            return None
        if method_name == 'Event':
            menu_id, event_id, _data, _timestamp = parameters.unpack()
            if event_id == 'clicked' and menu_id == 1:
                GLib.idle_add(self.on_activate, 0, 0)
            elif event_id == 'clicked' and menu_id == 2:
                GLib.idle_add(self.on_quit)
            elif event_id == 'clicked' and menu_id == 3:
                GLib.idle_add(self.on_update)
            invocation.return_value(None)
            return None
        if method_name == 'EventGroup':
            invocation.return_value(GLib.Variant('(ai)', ([],)))
            return None
        if method_name == 'AboutToShow':
            invocation.return_value(GLib.Variant('(b)', (False,)))
            return None
        if method_name == 'AboutToShowGroup':
            invocation.return_value(GLib.Variant('(aiai)', ([], [])))
            return None
        invocation.return_dbus_error('org.freedesktop.DBus.Error.UnknownMethod',
                                     f'unknown dbusmenu method {method_name}')
        return None

    def unregister(self):
        if self._watcher_sub_id:
            self.conn.signal_unsubscribe(self._watcher_sub_id)
        if self._item_reg_id:
            self.conn.unregister_object(self._item_reg_id)
        if self._menu_reg_id:
            self.conn.unregister_object(self._menu_reg_id)
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
