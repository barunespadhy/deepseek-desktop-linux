"""DeepSeek Harness for Linux: GTK4 + WebKitGTK desktop shell.

Shows a splash screen while the bundled DeepSeek Harness server boots, then
one WebKit window on the authenticated `dsh web` URL. Owns the SNI app
indicator, the appearance bridge (corner radius + custom CSS style sheet),
and the git update engine, and relays every state through the injected
`dshDesktop` carrier consumed by the settings UI.
"""

import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('WebKit', '6.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, WebKit

from .config import Config
from .server import DshServer, free_port
from .tray import StatusNotifierItem
from .updater import Updater, UpdateError
from .icons_data import ICONS

APP_ID = os.environ.get('DSH_LINUX_APP_ID', 'com.deepseek.dsh-linux')
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_JS_PATH = os.path.join(PROJECT_ROOT, 'dsh_linux', 'bridge.js')

# WebKitGTK 6.0 enum values (the typelib exposes anonymous enums).
DOCUMENT_START = 0  # WebKitUserScriptInjectionTime.DOCUMENT_START
TOP_FRAME = 1  # WebKitUserContentInjectedFrames.TOP_FRAME
ALL_FRAMES = 0  # WebKitUserContentInjectedFrames.ALL_FRAMES
USER_LEVEL = 0  # WebKitUserStyleLevel.USER
LOAD_FINISHED = 3  # WebKit.LoadEvent.FINISHED

# Blender's splash image is 570x300; this one started 50% larger per dimension
# (855x450) and was then reduced by 20%.
SPLASH_WIDTH = 684
SPLASH_HEIGHT = 360

# Splash palette follows the DeepSeek UI theme tokens (ui-theme
# design-platform.css): white base + near-black text for light, #151517 base +
# near-white text for dark. The sampled colors override these after first run.
DEFAULT_PALETTE = {
    'light': {'bg': '#ffffff', 'fg': '#0f1115'},
    'dark': {'bg': '#151517', 'fg': '#f9fafb'},
}


def rgba(color: str, alpha: float) -> str:
    """One CSS color (hex or `rgb(r, g, b)`) as an `rgba(r, g, b, a)` value."""
    match = re.search(r'([0-9]+)\s*[, ]\s*([0-9]+)\s*[, ]\s*([0-9]+)', color)
    if match:
        r, g, b = (int(group) for group in match.groups())
        return f'rgba({r}, {g}, {b}, {alpha})'
    value = color.lstrip('#')
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return f'rgba({r}, {g}, {b}, {alpha})'


def splash_css(bg: str, fg: str) -> str:
    """The GTK stylesheet for the splash window in the given theme colors."""
    return (
        f'window.splash {{ background-color: {bg}; }}\n'
        '.splash-logo-row { background: transparent; }\n'
        f'.splash-title {{ color: {fg}; font-weight: 700; font-size: 32px; }}\n'
        f'.splash-sub {{ color: {rgba(fg, 0.7)}; font-size: 15px; }}\n'
        f'.splash-status {{ color: {rgba(fg, 0.45)}; font-size: 13px; }}\n'
        '.splash-error { color: #ff7a7a; font-size: 13px; }\n'
        f'window.titlebar {{ background-color: {bg}; }}\n'
    )


class DshLinuxApp(Gtk.Application):
    """The desktop application object."""

    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.config = None
        self.server = None
        self.updater = None
        self.tray = None
        self.user_content = None
        self.window = None
        self.webview = None
        self.splash = None
        self.splash_status_label = None
        self.first_load_done = False
        self.presentation = {'phase': 'idle'}
        self.presentation_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------------

    def resolve_splash_palette(self):
        """The splash theme: last sampled UI colors, else the system theme."""
        bg = self.config.get('splashBg')
        fg = self.config.get('splashFg')
        if bg and fg:
            return bg, fg
        dark = self.config.get('splashDark')
        if dark is None:
            dark = self.system_prefers_dark()
        return DEFAULT_PALETTE['dark' if dark else 'light'].values()

    def system_prefers_dark(self):
        """Best-effort desktop color-scheme read (GNOME/KDE/GTK)."""
        try:
            from gi.repository import Gio
            settings = Gio.Settings.new('org.gnome.desktop.interface')
            scheme = settings.get_string('color-scheme')
            if scheme in ('prefer-dark', 'dark'):
                return True
            if scheme == 'prefer-light':
                return False
        except Exception:
            pass
        try:
            theme = Gtk.Settings.get_default().get_property('gtk-theme-name') or ''
            return 'dark' in theme.lower()
        except Exception:
            return False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.config = Config(PROJECT_ROOT)
        self.log('starting dsh-linux')

        self._splash_bg, self._splash_fg = self.resolve_splash_palette()
        provider = Gtk.CssProvider()
        provider.load_from_string(splash_css(self._splash_bg, self._splash_fg))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        port = free_port(int(self.config.get('port') or 3080))
        self.config.set('port', port)
        repo = self.config.repo_path()
        self.server = DshServer(
            repo, port,
            self.config.get('dshHome'), self.config.dir, self.log, config=self.config)
        self.updater = Updater(repo, self.config, self.config.dir, self.log)
        self.updater.on_presentation = self.on_update_presentation
        self.updater.restart_callback = self.restart_server

        try:
            self.tray = StatusNotifierItem(
                self.on_tray_activate, self.on_tray_quit, on_update=self.on_tray_update)
            self.tray.set_icon_style(self.config.get('iconStyle') or 'coloured')
            self.tray.set_visible(bool(self.config.get('appindicatorEnabled')))
        except Exception as error:  # a desktop without a session bus still runs the app
            self.log(f'app indicator unavailable: {error}')
            self.tray = None

    def do_activate(self):
        if self.window is not None:
            self.window.present()
            return
        self._ensure_main_window()
        self.show_splash()
        threading.Thread(target=self._startup_worker, daemon=True).start()

    def do_shutdown(self):
        if self.server is not None:
            self.server.stop()
        if self.tray is not None:
            try:
                self.tray.unregister()
            except Exception:
                pass
        Gtk.Application.do_shutdown(self)

    # -- helpers -------------------------------------------------------------------

    def log(self, message):
        line = f'dsh-linux: {message}'
        print(line, flush=True)
        try:
            os.makedirs(self.config.dir, exist_ok=True)
            with open(os.path.join(self.config.dir, 'dsh-linux.log'), 'a', encoding='utf-8') as handle:
                handle.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {line}\n')
        except OSError:
            pass

    def bridge_js(self):
        with open(BRIDGE_JS_PATH, encoding='utf-8') as handle:
            return handle.read()

    def apply_style_sheet(self):
        if self.user_content is None:
            return
        self.user_content.remove_all_style_sheets()
        self.user_content.add_style_sheet(WebKit.UserStyleSheet.new(
            self.config.appearance_css(), ALL_FRAMES, USER_LEVEL, [], []))

    def push_bridge(self, payload):
        if self.webview is None:
            return
        script = f'window.__DSH_BRIDGE__ && window.__DSH_BRIDGE__.push({json.dumps(payload)});'
        self.webview.evaluate_javascript(script, -1, None, None, None, None)

    def reply(self, request_id, value):
        if self.webview is None:
            return
        script = (
            f'window.__DSH_BRIDGE__ && window.__DSH_BRIDGE__.resolve('
            f'{json.dumps(request_id)}, true, JSON.parse({json.dumps(json.dumps(value))}));')
        self.webview.evaluate_javascript(script, -1, None, None, None, None)

    # -- splash ---------------------------------------------------------------------

    def show_splash(self):
        splash = Gtk.ApplicationWindow(application=self, title='DeepSeek Harness')
        splash.set_default_size(SPLASH_WIDTH, SPLASH_HEIGHT)
        splash.set_resizable(False)
        splash.set_decorated(False)
        splash.add_css_class('splash')
        # GTK4 has no manual positioning; transient + modal makes the
        # compositor center the splash over the (hidden) main window.
        if self.window is not None:
            splash.set_transient_for(self.window)
            splash.set_modal(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(32)
        box.set_margin_bottom(32)
        box.set_margin_start(48)
        box.set_margin_end(48)

        try:
            logo = GdkPixbuf.Pixbuf.new_from_file(os.path.join(PROJECT_ROOT, 'assets', 'deepseek-logo.png'))
            texture = Gdk.Texture.new_for_pixbuf(logo)
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_size_request(499, 106)
            picture.set_halign(Gtk.Align.CENTER)
            picture.set_margin_bottom(21)
            box.append(picture)
        except Exception as error:  # a broken image loader must not block startup
            self.log(f'splash logo unavailable: {error}')

        title = Gtk.Label(label='Harness')
        title.add_css_class('splash-title')
        title.set_halign(Gtk.Align.CENTER)
        box.append(title)

        status = Gtk.Label(label='Starting DeepSeek Harness…')
        status.add_css_class('splash-status')
        status.set_halign(Gtk.Align.CENTER)
        status.set_margin_top(14)
        box.append(status)

        splash.set_child(box)
        splash.present()
        self.splash = splash
        self.splash_status_label = status

    def splash_status(self, text):
        GLib.idle_add(self._splash_status_main, text)

    def _splash_status_main(self, text):
        if self.splash_status_label is not None:
            self.splash_status_label.set_label(text)
            if text.startswith('Failed'):
                self.splash_status_label.add_css_class('splash-error')
        return False

    def splash_fail(self, message):
        self.log(f'startup failed: {message}')
        GLib.idle_add(self._splash_fail_main, message)

    def _splash_fail_main(self, message):
        if self.splash_status_label is not None:
            self.splash_status_label.set_label(f'Failed to start: {message}')
            self.splash_status_label.add_css_class('splash-error')
        GLib.timeout_add_seconds(12, self.quit)
        return False

    # -- startup worker -----------------------------------------------------------------

    def _startup_worker(self):
        try:
            self.updater.ensure_repo(progress=self.splash_status)
            if not self.server.is_built():
                self.updater.ensure_built(progress=self.splash_status)
            self.splash_status('Starting DeepSeek Harness…')
            self.server.start()
            self.log('waiting for the web server…')
            url = self.server.wait_ready(timeout=180.0)
            self.log(f'server ready: {url[:60]}')
        except UpdateError as error:
            self.splash_fail(error.message)
            return
        except Exception as error:  # show startup failures instead of dying silently
            self.splash_fail(str(error) or repr(error))
            traceback.print_exc()
            return
        GLib.idle_add(self._load_main_window, url)

    def _ensure_main_window(self):
        """Create the window shell (titlebar, webview, bridge) before the
        server is ready so the splash can center over it."""
        window = Gtk.ApplicationWindow(application=self, title='DeepSeek Harness')
        window.set_default_size(int(self.config.get('windowWidth') or 1280),
                                int(self.config.get('windowHeight') or 820))
        window.set_size_request(880, 560)
        window.set_icon_name('deepseek-harness')
        window.connect('close-request', self.on_window_close)

        header = Gtk.HeaderBar()
        header.add_css_class('titlebar')
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        try:
            # High-resolution source so the small titlebar glyph stays crisp.
            glyph = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                os.path.join(PROJECT_ROOT, 'assets', 'icon-coloured-128.png'), 26, 26, True)
            picture = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(glyph))
            picture.set_margin_top(4)
            picture.set_margin_bottom(4)
            brand.append(picture)
        except Exception as error:  # the brand mark is decorative
            self.log(f'window logo unavailable: {error}')
        header.pack_start(brand)
        window.set_titlebar(header)

        webview = WebKit.WebView()
        # WebKitGTK 6.0: the view owns its user content manager; attach the
        # bridge script and the appearance style sheet before the first load.
        self.user_content = webview.get_user_content_manager()
        self.user_content.register_script_message_handler('dshlinux')
        self.user_content.add_script(WebKit.UserScript.new(
            self.bridge_js(), TOP_FRAME, DOCUMENT_START, [], []))
        self.user_content.connect('script-message-received::dshlinux', self.on_script_message)
        self.apply_style_sheet()
        settings = webview.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_javascript_can_access_clipboard(True)
        webview.connect('load-changed', self.on_load_changed)
        webview.connect('decide-policy', self.on_decide_policy)
        window.set_child(webview)

        self.window = window
        self.webview = webview
        self.app_origin = None

    def _load_main_window(self, url):
        self.log(f'loading {url[:60]} into the main window')
        parsed = urllib.parse.urlparse(url)
        self.app_origin = f'{parsed.scheme}://{parsed.netloc}'
        self.webview.load_uri(url)
        self.log('load started')
        # The window stays hidden until the first page finishes loading; the
        # splash keeps covering startup so the user never sees a blank shell.

    # -- navigation policy ----------------------------------------------------------
    # Link clicks inside DeepSeek Harness stay in the webview; links that point
    # anywhere else open the system default browser instead of hijacking the
    # app window.

    def is_external_uri(self, uri):
        if not uri.startswith(('http://', 'https://')):
            return False
        origin = urllib.parse.urlparse(uri)
        app_origin = self.app_origin or ''
        return f'{origin.scheme}://{origin.netloc}' != app_origin

    def open_external_uri(self, uri):
        if os.environ.get('DSH_LINUX_URI_PROBE'):
            self.log(f'external link: {uri}')
            return
        try:
            # Portal-aware launcher: works for packaged installs and inside
            # the Flatpak sandbox, and honours the user's default browser.
            Gtk.UriLauncher.new(uri).launch(self.window, None, None)
        except Exception:
            try:
                Gio.AppInfo.launch_default_for_uri_async(uri, None, None, None)
            except Exception as error:
                self.log(f'could not open external link {uri}: {error}')

    def on_decide_policy(self, _webview, decision, decision_type):
        # WebKitPolicyDecisionType: 0 = NAVIGATION_ACTION, 1 = NEW_WINDOW_ACTION.
        if decision_type == 1:  # target=_blank / window.open
            try:
                self.open_external_uri(decision.get_request().get_uri())
            except Exception:
                pass
            decision.ignore()
            return True
        if decision_type == 0:
            try:
                action = decision.get_navigation_action()
                uri = action.get_request().get_uri()
                # WebKitNavigationType: LINK_CLICKED = 0. A user-gesture
                # navigation of any type (SPA hand-offs included) to another
                # origin belongs in the browser, not the app window.
                if self.is_external_uri(uri) \
                        and (action.get_navigation_type() == 0 or action.is_user_gesture()):
                    self.open_external_uri(uri)
                    decision.ignore()
                    return True
            except Exception:
                pass
        return False

    def on_load_changed(self, webview, event):
        if event != LOAD_FINISHED:
            return
        first = not self.first_load_done
        if first:
            self.first_load_done = True
            if self.splash is not None:
                self.splash.destroy()
                self.splash = None
                self.splash_status_label = None
            self.window.present()
            self.log('interface loaded; showing main window')
            # The splash colors follow the UI theme; sample it once the page
            # paints, then keep following theme switches.
            self.sample_theme()
            GLib.timeout_add_seconds(60, self._theme_poll)
            if os.environ.get('DSH_LINUX_SELFTEST'):
                GLib.timeout_add_seconds(8, self._selftest_dump)
        # A fresh page load means any in-flight update finished: reset the state
        # so an applied update leaves the UI idle again.
        with self.presentation_lock:
            self.presentation = {'phase': 'idle'}
        self.push_bridge({'updates': {'phase': 'idle'}, 'appearance': self.config.appearance_state()})
        threading.Thread(target=self.updater.check, kwargs={'silent': True}, daemon=True).start()

    def _theme_poll(self):
        """Periodic theme sampling for the next launch's splash palette."""
        self.sample_theme()
        return True

    def sample_theme(self):
        """Read the live DeepSeek UI theme and persist it for the splash."""
        if self.webview is None:
            return
        script = (
            "JSON.stringify({dark: document.body.hasAttribute('data-ds-dark-theme'),"
            "bg: getComputedStyle(document.body).getPropertyValue('--dsw-alias-bg-base').trim(),"
            "fg: getComputedStyle(document.body).getPropertyValue('--dsw-alias-label-primary').trim()})")

        def got(_wv, result, _u):
            try:
                payload = json.loads(self.webview.evaluate_javascript_finish(result).to_string())
            except Exception as error:
                self.log(f'theme sample failed: {error}')
                return
            dark = bool(payload.get('dark'))
            bg = payload.get('bg') or ''
            fg = payload.get('fg') or ''
            if not ((bg.startswith('rgb') or bg.startswith('#'))
                    and (fg.startswith('rgb') or fg.startswith('#'))):
                self.log(f'theme sample returned unusable colors: {payload}')
                return
            if self.config.get('splashDark') != dark:
                self.config.set('splashDark', dark)
            if self.config.get('splashBg') != bg:
                self.config.set('splashBg', bg)
            if self.config.get('splashFg') != fg:
                self.config.set('splashFg', fg)
            self.log(f'theme sampled: dark={dark} bg={bg} fg={fg}')

        self.webview.evaluate_javascript(script, -1, None, None, None, got, None)

    def on_window_close(self, _window):
        if self.tray is not None and bool(self.config.get('appindicatorEnabled')):
            self.window.hide()
            return True  # keep running in the status area
        self.quit()
        return True

    # -- tray ---------------------------------------------------------------------------

    def on_tray_activate(self, _x=0, _y=0):
        if self.window is None:
            return
        if self.window.is_visible():
            self.window.hide()
        else:
            self.window.present()

    def on_tray_quit(self):
        self.quit()

    def on_tray_update(self):
        # The tray's 'Update available' entry: open the window (the red badges
        # in the settings menu and Updates section show what is new).
        if self.window is not None:
            self.window.present()

    # -- bridge requests -------------------------------------------------------------------

    def on_script_message(self, _user_content, js_value):
        try:
            message = json.loads(js_value.to_string())
        except (ValueError, TypeError):
            return
        if message.get('type') == 'open':
            # DOM-level interception of an external link (account, top-up,
            # and other outbound anchors the SPA would handle in JavaScript).
            self.open_external_uri(str(message.get('uri') or ''))
            return
        if message.get('type') != 'req':
            return
        request_id = message.get('id')
        method = message.get('method')
        args = message.get('args') or []
        self.log(f'bridge request: {method}')
        try:
            self.reply(request_id, self.handle_bridge(method, args))
        except Exception as error:
            self.log(f'bridge {method} failed: {error}')
            self.reply_error(request_id, str(error))

    def reply_error(self, request_id, message):
        if self.webview is None:
            return
        script = (
            f'window.__DSH_BRIDGE__ && window.__DSH_BRIDGE__.resolve('
            f'{json.dumps(request_id)}, false, {json.dumps(message)});')
        self.webview.evaluate_javascript(script, -1, None, None, None, None)

    def handle_bridge(self, method, args):
        if method == 'updates.status':
            with self.presentation_lock:
                return dict(self.presentation)
        if method == 'updates.open':
            self.updater.push(phase='checking', percent=1)
            threading.Thread(target=self.updater.check, kwargs={'silent': False}, daemon=True).start()
            return {}
        if method == 'updates.channel':
            return self.config.get('channel') or 'rc'
        if method == 'updates.setChannel':
            channel = args[0] if args else 'rc'
            if channel not in ('rc', 'beta'):
                raise ValueError(f'unknown channel {channel!r}')
            self.config.set('channel', channel)
            self.updater.push(phase='checking', percent=1)
            threading.Thread(target=self.updater.check, kwargs={'force': True, 'silent': False}, daemon=True).start()
            return {}
        if method == 'updates.apply':
            self.updater.apply()
            return {}
        if method == 'appearance.status':
            return self.config.appearance_state()
        if method == 'appearance.setCornerRadiusScale':
            scale = max(0.0, min(2.0, float(args[0])))
            self.config.set('cornerRadiusScale', scale)
            self.apply_style_sheet()
            self.push_bridge({'appearance': self.config.appearance_state()})
            return {}
        if method == 'appearance.setCustomCss':
            self.config.set('customCss', str(args[0]))
            self.apply_style_sheet()
            self.push_bridge({'appearance': self.config.appearance_state()})
            return {}
        if method == 'appearance.setAppindicatorEnabled':
            enabled = bool(args[0])
            self.config.set('appindicatorEnabled', enabled)
            if self.tray is not None:
                self.tray.set_visible(enabled)
            self.push_bridge({'appearance': self.config.appearance_state()})
            return {}
        if method == 'appearance.setIconStyle':
            style = args[0] if args else 'coloured'
            if style not in ('coloured', 'symbolic'):
                raise ValueError(f'unknown icon style {style!r}')
            self.config.set('iconStyle', style)
            if self.tray is not None:
                self.tray.set_icon_style(style)
            self.push_bridge({'appearance': self.config.appearance_state()})
            return {}
        raise ValueError(f'unknown bridge method {method!r}')

    # -- updates presentation -------------------------------------------------------------------

    def on_update_presentation(self, presentation):
        with self.presentation_lock:
            self.presentation = dict(presentation)
        GLib.idle_add(self._push_presentation, dict(presentation))

    def _push_presentation(self, presentation):
        self.push_bridge({'updates': presentation})
        if self.tray is not None:
            version = presentation.get('version') if presentation.get('phase') == 'available' else None
            try:
                self.tray.set_update_available(version)
            except Exception:
                pass
        return False

    def restart_server(self):
        """Restart dsh and reload the page; runs on the updater worker thread."""
        if self.server is None:
            raise RuntimeError('server not started')
        url = self.server.restart(timeout=180.0)
        GLib.idle_add(self._reload_webview, url)
        return url

    def _reload_webview(self, url):
        if self.webview is not None:
            self.webview.load_uri(url)
        return False

    # -- self-test (DSH_LINUX_SELFTEST=1) --------------------------------------

    def _selftest_step(self, script, label, next_step=None, delay=1.5):
        """Evaluate one probe in the real webview and log the result."""

        def run():
            def got(_wv, result, _u):
                try:
                    value = self.webview.evaluate_javascript_finish(result).to_string()
                except Exception:
                    value = '<eval error>'
                self.log(f'selftest {label}: {value[:500]}')
                if next_step is not None:
                    GLib.timeout_add(int(delay * 1000), next_step)
                else:
                    self.quit()
            if self.webview is not None:
                self.webview.evaluate_javascript(script, -1, None, None, None, got, None)

        return run

    def _selftest_dump(self):
        return self._selftest_step(
            "JSON.stringify({inert:!!document.querySelector('#root[inert]'),"
            "dialogs:document.querySelectorAll('[role=dialog]').length,"
            "body:(document.body?document.body.innerText:'').replace(/\\n+/g,' | ').slice(0,600),"
            "onboarding:!!(document.body&&document.body.innerText.includes('onboard')),"
            "settingsBtn:!!document.querySelector('[aria-label=\"Settings\"]')})",
            'dom-dump', self._selftest_dump2, 6)()

    def _selftest_dump2(self):
        return self._selftest_step(
            "JSON.stringify({buttonCount:document.querySelectorAll('button').length,"
            "settingsLike:[...document.querySelectorAll('button')].filter(b=>"
            "((b.getAttribute('aria-label')||'').toLowerCase().includes('settings')||"
            "(b.textContent||'').trim().toLowerCase()==='settings')).length})",
            'dom-dump2', self._selftest_open_settings, 1)()

    def _selftest_open_settings(self):
        return self._selftest_step(
            "(function(){const b=[...document.querySelectorAll('button')].find(x=>"
            "(x.getAttribute('aria-label')||'')==='Account menu');"
            "if(!b)return 'no account button';b.click();return 'clicked account';})()",
            'open-account', self._selftest_open_settings2)()

    def _selftest_open_settings2(self):
        return self._selftest_step(
            "(function(){const m=[...document.querySelectorAll('[role=\"menuitem\"], [role=\"menu\"] button, [role=\"menu\"] [role=\"menuitem\"]')]"
            ".find(x=>(x.textContent||'').trim().toLowerCase().includes('settings'));"
            "if(!m)return 'no settings item';m.click();return 'clicked settings item';})()",
            'open-settings', self._selftest_nav)()

    def _selftest_nav(self):
        return self._selftest_step(
            "JSON.stringify([...document.querySelectorAll('[role=\"dialog\"] nav button')].map(b=>b.textContent.trim()))",
            'nav-sections', self._selftest_appearance)()

    def _selftest_appearance(self):
        return self._selftest_step(
            "(function(){const c=[...document.querySelectorAll('[role=\"dialog\"] nav button')]"
            ".find(b=>(b.textContent||'').includes('Appearance'));if(!c)return 'no cell';c.click();return 'clicked';})()",
            'appearance-click', self._selftest_appearance_read)()

    def _selftest_appearance_read(self):
        return self._selftest_step(
            "(function(){const d=document.querySelector('[role=\"dialog\"]');if(!d)return 'no dialog';"
            "const t=d.textContent||'';return JSON.stringify({cornerRadius:t.includes('Corner radius'),"
            "slider:!!d.querySelector('input[type=range]'),customCss:t.includes('Custom CSS'),"
            "textarea:!!d.querySelector('textarea'),appIndicator:t.includes('App indicator'),"
            "switch:!!d.querySelector('[role=switch]'),themeCubes:t.includes('Light')&&t.includes('Dark')&&t.includes('System'),"
            "fontRow:t.includes('Font')});})()",
            'appearance-content', self._selftest_general)()

    def _selftest_general(self):
        return self._selftest_step(
            "(function(){const c=[...document.querySelectorAll('[role=\"dialog\"] nav button')]"
            ".find(b=>(b.textContent||'').trim()==='General');if(!c)return 'no cell';c.click();return 'clicked';})()",
            'general-click', self._selftest_general_read)()

    def _selftest_general_read(self):
        return self._selftest_step(
            "(function(){const d=document.querySelector('[role=\"dialog\"]');const t=d?(d.textContent||''):'';"
            "return JSON.stringify({hasCustomCss:t.includes('Custom CSS'),hasLanguage:t.includes('Language'),"
            "hasThemeCubes:t.includes('Light')&&t.includes('Dark')&&t.includes('System')});})()",
            'general-content', self._selftest_updates)()

    def _selftest_updates(self):
        return self._selftest_step(
            "(function(){const c=[...document.querySelectorAll('[role=\"dialog\"] nav button')]"
            ".find(b=>(b.textContent||'').includes('Updates'));if(!c)return 'no cell';c.click();return 'clicked';})()",
            'updates-click', self._selftest_updates_read)()

    def _selftest_updates_read(self):
        return self._selftest_step(
            "(function(){const d=document.querySelector('[role=\"dialog\"]');if(!d)return 'no dialog';"
            "const t=d.textContent||'';const buttons=d?[...d.querySelectorAll('button')].map(b=>b.textContent.trim()):[];"
            "return JSON.stringify({channel:t.includes('Update channel'),rc:t.includes('Release candidate'),"
            "beta:t.includes('Beta'),updateButton:buttons.some(x=>x==='Update'),"
            "currentVersion:t.includes('Current version')});})()",
            'updates-content', self._selftest_badge)()

    def _selftest_badge(self):
        return self._selftest_step(
            "(function(){window.__DSH_BRIDGE__&&window.__DSH_BRIDGE__.push({updates:{phase:'available',version:'dsh-v9.9.9-rc.1'}});"
            "return 'pushed available';})()",
            'badge-push', self._selftest_badge_read, 1.2)()

    def _selftest_badge_read(self):
        return self._selftest_step(
            "(function(){const d=document.querySelector('[role=\"dialog\"]');if(!d)return 'no dialog';"
            "const cell=[...d.querySelectorAll('nav button')].find(b=>(b.textContent||'').includes('Updates'));"
            "return JSON.stringify({navCellChildren:cell?cell.children.length:-1,"
            "triggerDot:!!document.querySelector('[aria-label=\"A new version is available.\"]'),"
            "availableVersion:(d.textContent||'').includes('dsh-v9.9.9-rc.1')});})()",
            'badge-read', self._selftest_appearance_write)()

    def _selftest_appearance_write(self):
        return self._selftest_step(
            "(function(){const c=[...document.querySelectorAll('[role=\"dialog\"] nav button')]"
            ".find(b=>(b.textContent||'').includes('Appearance'));if(!c)return 'no cell';c.click();return 'clicked';})()",
            'appearance-click2', self._selftest_appearance_write2)()

    def _selftest_appearance_write2(self):
        return self._selftest_step(
            "(function(){const d=document.querySelector('[role=\"dialog\"]');if(!d)return 'no dialog';"
            "const slider=d.querySelector('input[type=range]');if(!slider)return 'no slider';"
            "const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
            "setter.call(slider,'1.5');slider.dispatchEvent(new Event('input',{bubbles:true}));"
            "const sw=d.querySelector('[role=switch]');if(sw)sw.click();"
            "const ta=d.querySelector('textarea');if(!ta)return 'slider set, no textarea';"
            "const tsetter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;"
            "tsetter.call(ta,'.x-test { border-radius: 2px; }');ta.dispatchEvent(new Event('input',{bubbles:true}));"
            "return 'writes dispatched';})()",
            'appearance-writes', self._selftest_verify, 2.5)()

    def _selftest_link(self):
        # A page-level click handler would steer the second anchor SPA-style;
        # the bridge still intercepts it because its capture listener runs first.
        return self._selftest_step(
            "(function(){"
            "var a=document.createElement('a');"
            "a.href='https://example.com/dsh-linux-test';a.textContent='x';"
            "document.body.appendChild(a);a.click();"
            "var b=document.createElement('a');"
            "b.href='https://example.org/dsh-linux-account-test';b.textContent='y';"
            "document.body.appendChild(b);"
            "b.addEventListener('click',function(e){e.preventDefault();"
            "location.assign(b.href);});"
            "b.click();return 'clicked';})()",
            'external-link', self._selftest_quit, 1.0)()

    def _selftest_verify(self):
        return self._selftest_step(
            "(function(){const cs=getComputedStyle(document.documentElement);"
            "const el=document.getElementById('dsh-linux-user-css');"
            "return JSON.stringify({md:cs.getPropertyValue('--dsw-radius-md').trim(),"
            "inlineMd:document.documentElement.style.getPropertyValue('--dsw-radius-md').trim(),"
            "userCss:el?(el.textContent||'').slice(0,40):'missing'});})()",
            'appearance-verify', self._selftest_link, 0)()

    def _selftest_quit(self):
        # Give the debounced CSS write time to flush, then leave.
        self.quit()
        return False


def main(argv=None):
    """Run the desktop shell."""
    app = DshLinuxApp()
    code = app.run(argv if argv is not None else sys.argv)
    if app.window is None:
        print(
            'dsh-linux: the application did not start. A running instance may already own '
            'the session name, or the session bus is unavailable.', file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
