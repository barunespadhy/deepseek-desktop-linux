"""DeepSeek Harness server process management.

Spawns the built `dsh` CLI (`node apps/cli/lib/bin.js web`) from the tracked
git checkout, reads the authenticated URL from the `dsh web: <url>` startup
line, and supervises restarts (used after an update applies).
"""

import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from .paths import augment_path

URL_LINE_RE = re.compile(r'dsh web: (https?://\S+)')


def free_port(preferred: int, limit: int = 200) -> int:
    """Return `preferred` when free, otherwise the next free port."""
    for port in range(preferred, preferred + limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError(f'no free port found in {preferred}..{preferred + limit}')


class DshServer:
    """Owns one dsh web child process and its advertised URL."""

    def __init__(self, repo: str, port: int, dsh_home: str, config_dir: str, log, config=None):
        self.repo = repo
        self.port = port
        self.dsh_home = dsh_home
        self.config_dir = config_dir
        self.log = log
        self.config = config
        self.proc = None
        self.url = None
        self.stopped = False
        self._url_event = threading.Event()
        self._reader = None

    def bin_path(self) -> str:
        return os.path.join(self.repo, 'apps', 'cli', 'lib', 'bin.js')

    def is_built(self) -> bool:
        return os.path.exists(self.bin_path())

    def node_bin(self) -> str:
        node = self.config.tool('node') if self.config is not None else None
        if node is None:
            raise RuntimeError(
                'Node.js was not found. Install Node.js >= 24.9 (https://nodejs.org) '
                'or set DSH_LINUX_NODE to its executable path.')
        return node

    def build_env(self):
        env = dict(os.environ)
        env['DSH_HOME'] = os.path.expanduser(self.dsh_home)
        env['NO_COLOR'] = '1'
        env['NODE_NO_WARNINGS'] = '1'
        # Desktop-launched processes miss shell PATH additions; keep the child
        # able to reach version-managed binaries, but never shadow the
        # resolved node itself.
        env['PATH'] = augment_path()
        # App-local pnpm store/cache so builds never touch the user's global store.
        env['PNPM_STORE_DIR'] = os.path.join(self.config_dir, 'pnpm-store')
        env['npm_config_cache'] = os.path.join(self.config_dir, 'npm-cache')
        return env

    def start(self):
        self.stopped = False
        self.url = None
        self._url_event.clear()
        command = [self.node_bin(), self.bin_path(), 'web', '--no-open', '--port', str(self.port)]
        self.log(f'spawning {" ".join(command)}')
        try:
            self.proc = subprocess.Popen(
                command,
                cwd=self.repo,
                env=self.build_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                f'Node.js was not found at {command[0]}. Install Node.js >= 24.9 '
                'or set DSH_LINUX_NODE to its executable path.') from error
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()

    def _read_output(self):
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip('\n')
            self.log(f'[dsh] {line}')
            match = URL_LINE_RE.search(line)
            if match and self.url is None:
                self.url = match.group(1)
                self._url_event.set()

    def wait_ready(self, timeout: float = 90.0) -> str:
        """Wait for the printed authenticated URL and a live HTTP answer.

        The token URL answers with a redirect that moves the token into a
        cookie, so an urllib probe without a cookie jar sees 401 even though
        the server is fully up — any HTTP status (including 401) counts as
        ready; the WebKit view owns the cookie-authenticated load.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.url is not None:
                try:
                    with urllib.request.urlopen(self.url, timeout=5) as response:
                        self.log(f'server answered with status {response.status}')
                        return self.url
                except urllib.error.HTTPError as error:
                    self.log(f'server answered with status {error.code} (ready)')
                    return self.url
                except (urllib.error.URLError, OSError) as error:
                    self.log(f'server probe failed: {error}')
            time.sleep(0.3)
        raise TimeoutError(f'dsh web did not become ready within {timeout}s')

    def restart(self, timeout: float = 90.0) -> str:
        """Stop and respawn; returns the new authenticated URL."""
        self.stop()
        time.sleep(0.4)
        self.start()
        return self.wait_ready(timeout)

    def stop(self):
        self.stopped = True
        proc = self.proc
        self.proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
