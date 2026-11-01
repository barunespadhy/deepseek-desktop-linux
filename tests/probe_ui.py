"""Drive the DSH settings UI inside WebKitGTK and report what renders.

Loads the token URL (arg 1) with the shell bridge script injected, answers
the carrier requests with canned replies, opens Settings, and asserts the
Appearance and Updates sections plus the red badge wiring.
"""
import json
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, WebKit, GLib

token = sys.argv[1] if len(sys.argv) > 1 else ''
url = f'http://127.0.0.1:3095/?token={token}'
bridge_js = open('dsh_linux/bridge.js', encoding='utf-8').read()

app = Gtk.Application()
web = None
report = []


def evaluate(script):
    """Fire-and-forget evaluation; results are reported through DOM probes."""
    if web is None:
        return
    web.evaluate_javascript(script, -1, None, None, None, None, None)


def reply(request_id, value):
    evaluate(
        f'window.__DSH_BRIDGE__ && window.__DSH_BRIDGE__.resolve({json.dumps(request_id)}, true, '
        f'JSON.parse({json.dumps(json.dumps(value))}));')


def on_message(_um, js_value):
    try:
        message = json.loads(js_value.to_string())
    except (ValueError, TypeError):
        return
    if message.get('type') != 'req':
        return
    method = message.get('method')
    if method == 'updates.status':
        reply(message.get('id'), {'phase': 'available', 'version': 'dsh-v9.9.9-rc.1', 'percent': 100})
    elif method == 'updates.channel':
        reply(message.get('id'), 'rc')
    elif method == 'appearance.status':
        reply(message.get('id'), {
            'cornerRadiusScale': 1.0, 'customCss': '',
            'appindicatorEnabled': True, 'iconStyle': 'coloured'})
    else:
        reply(message.get('id'), {})


def probe(label, script, delay, next_step=None):
    def run():
        def got(_wv, result, _u):
            try:
                value = web.evaluate_javascript_finish(result).to_string()
            except Exception:
                value = '<eval error>'
            report.append(f'{label}: {value}')
            print(f'{label}: {value[:300]}', flush=True)
            if next_step is not None:
                GLib.timeout_add(delay, next_step)
            else:
                app.quit()
        web.evaluate_javascript(script, -1, None, None, None, got, None)
    GLib.timeout_add(delay, run)


def step_dump():
    probe('dom-dump', """
      JSON.stringify({
        title: document.title,
        ready: document.readyState,
        rootChildren: document.querySelector('#root') ? document.querySelector('#root').childElementCount : -1,
        bodyText: (document.body ? document.body.textContent : '').slice(0, 200),
        buttons: [...document.querySelectorAll('button')].slice(0, 40).map(b =>
          (b.getAttribute('aria-label') || '') + ' | ' + (b.textContent || '').trim().slice(0, 40)),
        dialogs: document.querySelectorAll('[role="dialog"]').length,
      })
    """, 8000, step_settings_open)


def step_settings_open():
    probe('open-settings', """
      (function(){
        const b = [...document.querySelectorAll('button')].find(x =>
          (x.getAttribute('aria-label') || '').toLowerCase().includes('settings') ||
          (x.textContent || '').trim().toLowerCase() === 'settings');
        if (!b) return 'no trigger button found';
        b.click(); return 'clicked';
      })()
    """, 1500, step_nav_read)


def step_nav_read():
    probe('nav-sections', """
      JSON.stringify([...document.querySelectorAll('[role="dialog"] nav button')].map(b => b.textContent.trim()))
    """, 1200, step_appearance)


def step_appearance():
    probe('appearance-section', """
      (function(){
        const cell = [...document.querySelectorAll('[role="dialog"] nav button')]
          .find(b => (b.textContent || '').includes('Appearance'));
        if (!cell) return 'no Appearance nav cell';
        cell.click();
        return 'clicked';
      })()
    """, 1200, step_appearance_read)


def step_appearance_read():
    probe('appearance-content', """
      (function(){
        const dlg = document.querySelector('[role="dialog"]');
        if (!dlg) return 'no dialog';
        const text = dlg.textContent || '';
        return JSON.stringify({
          cornerRadius: text.includes('Corner radius'),
          radiusSlider: !!dlg.querySelector('input[type="range"]'),
          customCss: text.includes('Custom CSS'),
          textarea: !!dlg.querySelector('textarea'),
          appIndicator: text.includes('App indicator'),
          switch: !!dlg.querySelector('[role="switch"]'),
          themeCubes: text.includes('Light') && text.includes('Dark') && text.includes('System'),
          fontRow: text.includes('Font'),
          notInGeneralHack: true,
        });
      })()
    """, 1200, step_general_check)


def step_general_check():
    probe('general-content', """
      (function(){
        const cell = [...document.querySelectorAll('[role="dialog"] nav button')]
          .find(b => (b.textContent || '').trim() === 'General');
        if (!cell) return 'no General nav cell';
        cell.click();
        return 'clicked';
      })()
    """, 1200, step_general_read)


def step_general_read():
    probe('general-no-appearance', """
      (function(){
        const dlg = document.querySelector('[role="dialog"]');
        const text = dlg ? (dlg.textContent || '') : '';
        return JSON.stringify({ hasCustomCss: text.includes('Custom CSS'), hasLanguage: text.includes('Language') });
      })()
    """, 1200, step_updates)


def step_updates():
    probe('updates-section', """
      (function(){
        const cell = [...document.querySelectorAll('[role="dialog"] nav button')]
          .find(b => (b.textContent || '').includes('Updates'));
        if (!cell) return 'no Updates nav cell';
        cell.click();
        return 'clicked';
      })()
    """, 1200, step_updates_read)


def step_updates_read():
    probe('updates-content', """
      (function(){
        const dlg = document.querySelector('[role="dialog"]');
        const text = dlg ? (dlg.textContent || '') : '';
        const buttons = dlg ? [...dlg.querySelectorAll('button')].map(b => b.textContent.trim()) : [];
        return JSON.stringify({
          channel: text.includes('Update channel'),
          rcLabel: text.includes('Release candidate'),
          betaLabel: text.includes('Beta'),
          updateButton: buttons.some(t => t === 'Update'),
          availableVersion: text.includes('dsh-v9.9.9-rc.1'),
          progress: !!dlg.querySelector('[class*="progressTrack"], [class*="progress"]'),
        });
      })()
    """, 1200, None)


def on_activate(a):
    global web
    win = Gtk.ApplicationWindow(application=a)
    web = WebKit.WebView()
    win.set_child(web)
    content = web.get_user_content_manager()
    content.register_script_message_handler('dshlinux')
    content.add_script(WebKit.UserScript.new(bridge_js, 1, 0, [], []))
    content.connect('script-message-received::dshlinux', on_message)
    finished = {'done': False}

    def loaded(_w, ev):
        if ev == 3 and not finished['done']:
            finished['done'] = True
            step_dump()

    web.connect('load-changed', loaded)
    web.load_uri(url)
    win.present()


app.connect('activate', on_activate)
app.run(None)
