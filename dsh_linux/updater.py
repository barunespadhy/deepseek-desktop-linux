"""Git-based update engine for the DeepSeek Harness checkout.

The tracked repository keeps the shell's UI changes as local commits on top of
upstream `master`; `refs/desktop/patch-base` and `refs/desktop/patch-tip`
record that commit range. An update fetches `origin`, re-applies the range
onto the channel target (`origin/master` for beta, the newest `dsh-v*-rc.*`
tag for release-candidate) with `git rebase --onto`, reinstalls dependencies,
rebuilds the Web artifacts, and asks the application to restart the server.
"""

import os
import shutil
import subprocess
import threading
import time

from .paths import augment_path

GIT_IDENTITY = {
    'GIT_AUTHOR_NAME': 'DeepSeek Harness Linux',
    'GIT_AUTHOR_EMAIL': 'dsh-linux@localhost',
    'GIT_COMMITTER_NAME': 'DeepSeek Harness Linux',
    'GIT_COMMITTER_EMAIL': 'dsh-linux@localhost',
}

PATCH_BASE_REF = 'refs/desktop/patch-base'
PATCH_TIP_REF = 'refs/desktop/patch-tip'


class UpdateError(Exception):
    """Classified failure with the Web presentation kind."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


class _ProgressPump:
    """Slowly advances `percent` through one phase so the bar keeps moving."""

    def __init__(self, push, phase: str, lo: int, hi: int, step: float = 0.7):
        self._push = push
        self._phase = phase
        self._hi = hi
        self._step = step
        self._stop = threading.Event()
        self._percent = lo
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self._step):
            self._percent = min(self._hi - 1, self._percent + 1)
            self._push(phase=self._phase, percent=self._percent)

    def finish(self):
        self._stop.set()


class Updater:
    """Owns update checking and application for one repository."""

    def __init__(self, repo: str, config, config_dir: str, log):
        self.repo = repo
        self.config = config
        self.config_dir = config_dir
        self.log = log
        self.presentation = {'phase': 'idle'}
        self.lock = threading.Lock()
        self.busy = threading.Event()
        self.on_presentation = None  # callable(dict) assigned by the application
        self.restart_callback = None  # callable() -> new URL, assigned by the application

    def push(self, **fields):
        with self.lock:
            self.presentation = dict(fields)
        callback = self.on_presentation
        if callback is not None:
            callback(self.presentation)

    # -- subprocess helpers -------------------------------------------------

    def git_bin(self) -> str:
        git = self.config.tool('git') if self.config is not None else None
        if git is None:
            raise UpdateError('check-network',
                              'git was not found. Install git or set DSH_LINUX_GIT to its executable path.')
        return git

    def git(self, *args, timeout: float = 300.0):
        env = dict(os.environ)
        env.update(GIT_IDENTITY)
        env['GIT_TERMINAL_PROMPT'] = '0'
        try:
            return subprocess.run(
                [self.git_bin(), '-C', self.repo, *args],
                env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise UpdateError('download-network', f'git {args[0]} timed out: {error}') from error

    def node_env(self):
        env = dict(os.environ)
        env.update(GIT_IDENTITY)
        env['NO_COLOR'] = '1'
        env['PATH'] = augment_path()
        # The bundled C compiler (vendor/cc) keeps first-run builds and git
        # updates independent of the host toolchain (dsh compiles a native
        # Node-API addon during build:artifacts).
        if self.config is not None:
            bundled_cc = os.path.join(self.config.project_root, 'vendor', 'cc', 'bin')
            if os.path.isdir(bundled_cc):
                env['PATH'] = os.pathsep.join([bundled_cc, env['PATH']])
        # Package postinstall scripts run bare `node`; make the resolved
        # runtime reachable even under a trimmed desktop PATH.
        node = self.config.tool('node') if self.config is not None else None
        if node:
            node_dir = os.path.dirname(node)
            if node_dir not in env['PATH'].split(os.pathsep):
                env['PATH'] = os.pathsep.join([node_dir, env['PATH']])
        env['PNPM_STORE_DIR'] = os.path.join(self.config_dir, 'pnpm-store')
        env['npm_config_cache'] = os.path.join(self.config_dir, 'npm-cache')
        return env

    def pnpm_command(self, *args):
        """The resolved pnpm invocation (node+corepack fallback included)."""
        pnpm = self.config.tool('pnpm') if self.config is not None else None
        if pnpm is not None:
            if pnpm.endswith(('.js', '.mjs', '.cjs')):
                node = self.config.tool('node')
                if node is None:
                    raise UpdateError('install', 'Node.js was not found; set DSH_LINUX_NODE.')
                return [node, pnpm, *args]
            return [pnpm, *args]
        corepack = self.config.tool('corepack') if self.config is not None else None
        node = self.config.tool('node') if self.config is not None else None
        if corepack is not None and node is not None:
            return [node, corepack, 'pnpm', *args]
        raise UpdateError(
            'install',
            'pnpm was not found. Install pnpm (https://pnpm.io/installation), run `corepack enable`, '
            'or set DSH_LINUX_PNPM to its executable path.')

    def pnpm(self, *args, timeout: float = 1800.0):
        command = self.pnpm_command(*args)
        try:
            return subprocess.run(
                command,
                cwd=self.repo, env=self.node_env(),
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise UpdateError('install', f'pnpm {" ".join(args)} timed out: {error}') from error
        except FileNotFoundError as error:
            raise UpdateError('install', f'pnpm was not found ({error}); set DSH_LINUX_PNPM.') from error

    def pnpm_failure(self, result, args):
        """The tail of a failed pnpm run (stdout carries most pnpm errors)."""
        tail = (result.stderr or result.stdout).strip()[-1200:]
        return tail or f'pnpm {" ".join(args)} exited with status {result.returncode}'

    # -- git facts -----------------------------------------------------------

    def patch_base(self):
        result = self.git('rev-parse', '--verify', PATCH_BASE_REF)
        return result.stdout.strip() if result.returncode == 0 else None

    def patch_tip(self):
        result = self.git('rev-parse', '--verify', PATCH_TIP_REF)
        return result.stdout.strip() if result.returncode == 0 else None

    def head(self):
        return self.git('rev-parse', 'HEAD').stdout.strip()

    def describe(self, ref: str) -> str:
        result = self.git('describe', '--tags', '--always', ref)
        return result.stdout.strip() or ref[:12]

    def target_for(self, channel: str):
        if channel == 'beta':
            result = self.git('rev-parse', 'origin/master')
            if result.returncode != 0:
                raise UpdateError('check-network', 'origin/master is not known; run a fetch first')
            target_hash = result.stdout.strip()
            return {'hash': target_hash, 'desc': self.describe('origin/master')}
        tags = self.git(
            'for-each-ref', '--sort=-version:refname', '--format=%(refname:short)',
            'refs/tags', 'dsh-v*rc*')
        for line in tags.stdout.splitlines():
            tag = line.strip()
            if tag:
                resolved = self.git('rev-parse', tag)
                if resolved.returncode == 0:
                    return {'hash': resolved.stdout.strip(), 'desc': tag}
        raise UpdateError('check-network', 'no release-candidate tags found on origin')

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.git('merge-base', '--is-ancestor', older, newer).returncode == 0

    def ensure_patch_refs(self):
        """Create the patch refs on first run: no local patches recorded yet."""
        if self.patch_base() is None:
            head = self.head()
            self.git('update-ref', PATCH_BASE_REF, head)
            self.git('update-ref', PATCH_TIP_REF, head)
        # Rebase needs an identity; set it repo-locally when unset.
        if self.git('config', 'user.email').stdout.strip() == '':
            self.git('config', 'user.email', 'dsh-linux@localhost')
        if self.git('config', 'user.name').stdout.strip() == '':
            self.git('config', 'user.name', 'DeepSeek Harness Linux')

    # -- public API -----------------------------------------------------------

    def current_version(self) -> str:
        return self.describe(self.head())

    def check(self, force: bool = False, silent: bool = False):
        """Fetch and compare against the configured channel target."""
        if self.busy.is_set():
            return
        try:
            self.ensure_patch_refs()
            fetched = self.git('fetch', 'origin', '--tags', '--prune', '--quiet', timeout=300.0)
            if fetched.returncode != 0:
                raise UpdateError('check-network', fetched.stderr.strip()[:200])
            target = self.target_for(self.config.get('channel') or 'rc')
            base = self.patch_base()
            if base is None or target['hash'] == base:
                self.push(phase='idle')
                return
            if not force and self.is_ancestor(target['hash'], base):
                # The channel's target is older than what is installed; never badge a downgrade.
                self.push(phase='idle')
                return
            self.push(phase='available', version=target['desc'])
        except UpdateError as error:
            if not silent:
                self.push(phase='error', failure=error.kind)
            self.log(f'update check failed: {error.message}')

    def apply(self):
        """Run the full update flow in a worker thread; progress arrives via push."""
        if self.busy.is_set():
            return
        self.busy.set()
        threading.Thread(target=self._apply_worker, daemon=True).start()

    def _apply_worker(self):
        try:
            self.ensure_patch_refs()
            self.push(phase='checking', percent=2)
            dirty = self.git('status', '--porcelain', '--untracked-files=no')
            if dirty.stdout.strip():
                raise UpdateError('install', 'working tree has uncommitted changes')
            fetched = self.git('fetch', 'origin', '--tags', '--prune', '--quiet', timeout=600.0)
            if fetched.returncode != 0:
                raise UpdateError('download-network', fetched.stderr.strip()[:200])
            target = self.target_for(self.config.get('channel') or 'rc')
            base = self.patch_base()
            self.push(phase='downloading', percent=12, version=target['desc'])
            if target['hash'] == base:
                self.push(phase='idle')
                return
            rebased = self.git('rebase', '--onto', target['hash'], base)
            if rebased.returncode != 0:
                self.git('rebase', '--abort')
                raise UpdateError('install', rebased.stderr.strip()[:300])
            self.git('update-ref', PATCH_BASE_REF, target['hash'])
            self.git('update-ref', PATCH_TIP_REF, self.head())
            install_pump = _ProgressPump(self.push, 'downloading', 20, 44)
            installed = self.pnpm('install')
            install_pump.finish()
            if installed.returncode != 0:
                raise UpdateError('install', self.pnpm_failure(installed, ['install']))
            self.push(phase='downloading', percent=45, version=target['desc'])
            build_pump = _ProgressPump(self.push, 'downloading', 45, 88)
            built = self.pnpm('run', 'build:artifacts', timeout=2400.0)
            build_pump.finish()
            if built.returncode != 0:
                raise UpdateError('install', self.pnpm_failure(built, ['run', 'build:artifacts']))
            self.push(phase='installing', percent=92, version=target['desc'])
            self.push(phase='ready', percent=100, version=target['desc'])
            if self.restart_callback is None:
                raise UpdateError('install', 'restart handler is not wired')
            self.restart_callback()
            self.log(f'update applied: {target["desc"]}')
        except UpdateError as error:
            self.log(f'update failed: {error.message}')
            self.push(phase='error', failure=error.kind)
        except Exception as error:  # unexpected failures stay visible, never silent
            self.log(f'update failed unexpectedly: {error!r}')
            self.push(phase='error', failure='install')
        finally:
            self.busy.clear()

    def ensure_built(self, progress=None):
        """First-run preparation: install dependencies and build artifacts."""
        self.ensure_patch_refs()
        if progress:
            progress('Preparing the DeepSeek Harness interface (first run)…')
        installed = self.pnpm('install')
        if installed.returncode != 0:
            raise UpdateError('install', self.pnpm_failure(installed, ['install']))
        if progress:
            progress('Building the interface…')
        built = self.pnpm('run', 'build:artifacts', timeout=2400.0)
        if built.returncode != 0:
            raise UpdateError('install', self.pnpm_failure(built, ['run', 'build:artifacts']))

    def ensure_repo(self, progress=None):
        """Provision the writable checkout a packaged install runs from.

        Package managers install files read-only under /usr or /app, so the
        first run materializes the bundled snapshot (or a fresh GitHub clone
        plus the bundled UI patches) into the per-user data directory. A
        snapshot already carries .git, the patch refs, and the UI commits, so
        later updates keep working unchanged.

        @param progress - optional splash-status callable.
        """
        if os.path.isdir(os.path.join(self.repo, '.git')):
            return
        snapshot = self.config.snapshot_dir() if self.config is not None else ''
        if snapshot and os.path.isdir(os.path.join(snapshot, '.git')):
            if progress:
                progress('Preparing the DeepSeek Harness interface (first run)…')
            os.makedirs(os.path.dirname(self.repo), exist_ok=True)
            shutil.copytree(snapshot, self.repo)
            self.ensure_patch_refs()
            return
        if progress:
            progress('Downloading DeepSeek Harness (first run)…')
        git = self.git_bin()
        os.makedirs(os.path.dirname(self.repo), exist_ok=True)
        cloned = subprocess.run(
            [git, 'clone', 'https://github.com/deepseek-ai/deepseek-harness.git', self.repo],
            env={**os.environ, **GIT_IDENTITY, 'GIT_TERMINAL_PROMPT': '0'},
            capture_output=True, text=True, timeout=1800.0)
        if cloned.returncode != 0:
            raise UpdateError('download-network', cloned.stderr.strip()[-300:])
        patches = self.config.patches_dir() if self.config is not None else ''
        if patches and os.path.isdir(patches):
            files = sorted(f for f in os.listdir(patches) if f.endswith('.patch'))
            if files:
                applied = subprocess.run(
                    [git, '-C', self.repo, 'am', *[os.path.join(patches, f) for f in files]],
                    env={**os.environ, **GIT_IDENTITY},
                    capture_output=True, text=True, timeout=300.0)
                if applied.returncode != 0:
                    subprocess.run([git, '-C', self.repo, 'am', '--abort'], capture_output=True)
                    raise UpdateError('install', applied.stderr.strip()[-300:])
                self.git('update-ref', PATCH_BASE_REF, f'HEAD~{len(files)}')
                self.git('update-ref', PATCH_TIP_REF, 'HEAD')
                return
        self.ensure_patch_refs()
