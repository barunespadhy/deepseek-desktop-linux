"""Persistent configuration for the DeepSeek Harness Linux shell."""

import json
import os
import threading

from .paths import find_program

DEFAULTS = {
    'cornerRadiusScale': 1.0,
    'customCss': '',
    'appindicatorEnabled': True,
    'iconStyle': 'coloured',  # 'coloured' | 'symbolic'
    'channel': 'rc',  # 'rc' | 'beta'
    'port': 3080,
    'dshHome': '~/.dsh',
    'windowWidth': 1280,
    'windowHeight': 820,
    'splashDark': None,
    'splashBg': '',
    'splashFg': '',
}

# Radius token bases mirrored from dsh ui-theme/styles/base.css.
RADIUS_BASES = {
    '--dsw-radius-xs': 4,
    '--dsw-radius-sm': 8,
    '--dsw-radius-md': 12,
    '--dsw-radius-lg': 16,
    '--dsw-radius-xl': 20,
    '--dsw-radius-panel': 28,
}


class Config:
    """JSON-backed configuration; falls back to in-memory when unwritable."""

    def __init__(self, project_root: str):
        self._lock = threading.Lock()
        self.project_root = project_root
        env_dir = os.environ.get('DSH_LINUX_CONFIG_DIR')
        if env_dir:
            self.dir = env_dir
        else:
            xdg = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
            self.dir = os.path.join(xdg, 'dsh-linux')
        self.path = os.path.join(self.dir, 'config.json')
        self.data = dict(DEFAULTS)
        if os.environ.get('DSH_LINUX_REPO'):
            self.data['dshRepo'] = os.environ['DSH_LINUX_REPO']
        if os.environ.get('DSH_LINUX_DSH_HOME'):
            self.data['dshHome'] = os.environ['DSH_LINUX_DSH_HOME']
        if os.environ.get('DSH_LINUX_PORT'):
            self.data['port'] = int(os.environ['DSH_LINUX_PORT'])
        self.load()

    def data_dir(self) -> str:
        """The per-user writable data root for the tracked checkout."""
        xdg = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
        return os.path.join(xdg, 'dsh-linux')

    def repo_path(self) -> str:
        """Where the DeepSeek Harness checkout lives (writable by design).

        Precedence: an explicit `dshRepo` config value, then the vendored
        third_party/dsh checkout (source/portable runs), then the per-user
        data dir (packaged installs, where /usr and /app are read-only).
        """
        with self._lock:
            explicit = self.data.get('dshRepo')
        if explicit and os.path.isdir(explicit):
            return explicit
        beside = os.path.join(self.project_root, 'third_party', 'dsh')
        if os.path.isdir(beside):
            return beside
        return os.path.join(self.data_dir(), 'dsh')

    def snapshot_dir(self) -> str:
        """The bundled checkout snapshot a packaged install provisions from."""
        env_snapshot = os.environ.get('DSH_LINUX_SNAPSHOT')
        if env_snapshot and os.path.isdir(env_snapshot):
            return env_snapshot
        return os.path.join(self.project_root, 'share', 'dsh-linux', 'dsh')

    def patches_dir(self) -> str:
        """The bundled UI patch files for the fresh-clone fallback."""
        return os.path.join(self.project_root, 'patches')

    def load(self):
        try:
            with open(self.path, encoding='utf-8') as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    if key in DEFAULTS or key == 'dshRepo' or key.endswith('Bin'):
                        self.data[key] = value
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(self.data, handle, indent=2)
            os.replace(tmp, self.path)
        except OSError as error:
            print(f'dsh-linux: config not persisted ({error}); running with in-memory settings')

    def get(self, key):
        with self._lock:
            return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        with self._lock:
            self.data[key] = value
            self.save()

    def tool(self, name):
        """Resolve an external tool, reusing a previously discovered path.

        Desktop-launched processes miss the shell's PATH additions (nvm,
        volta, ~/.local/bin, …), so discovery searches those locations too.

        @param name - 'node', 'pnpm', 'corepack', or 'git'.
        @returns the absolute executable path, or None when not found.
        """
        key = f'{name}Bin'
        with self._lock:
            cached = self.data.get(key)
        if cached and os.path.isfile(cached) and os.access(cached, os.X_OK):
            return cached
        found = find_program(name)
        if found:
            self.set(key, found)
        return found

    def appearance_state(self):
        """The JSON state object the web Appearance bridge consumes."""
        with self._lock:
            return {
                'cornerRadiusScale': float(self.data.get('cornerRadiusScale', 1.0)),
                'customCss': str(self.data.get('customCss', '')),
                'appindicatorEnabled': bool(self.data.get('appindicatorEnabled', True)),
                'iconStyle': self.data.get('iconStyle', 'coloured'),
            }

    def appearance_css(self):
        """The user stylesheet: scaled radius tokens plus the custom CSS.

        The tokens carry `!important`: the injected sheet sits in the user
        origin, whose *normal* declarations lose to the page's author styles,
        so without the important tier the DeepSeek UI's own `:root` values
        win and the corner-radius control has no visible effect.
        """
        state = self.appearance_state()
        scale = max(0.0, min(2.0, state['cornerRadiusScale']))
        tokens = ' '.join(
            f'{name}: {round(base * scale * 10) / 10}px !important;' for name, base in RADIUS_BASES.items())
        return f':root {{ {tokens} }}\n{state["customCss"]}'
