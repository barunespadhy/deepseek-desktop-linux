#!/usr/bin/env bash
# Build the Flatpak bundle (SDK/runtime + app) and copy it into dist/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="com.deepseek.dshlinux"
VERSION="0.1.0"
BUILD_DIR="$ROOT/build/flatpak-build"
# Generated sources/manifest live under build/ (git-ignored); the tracked
# flatpak/ directory only holds the bundle scripts and the desktop file.
SRC_DIR="$ROOT/build/flatpak-src"
OUT="$ROOT/dist"

# glib's temp files fall back to /var/tmp (read-only in restricted
# environments); force them onto /tmp, which supports O_TMPFILE.
export TMPDIR="${TMPDIR:-/tmp}"
# flatpak-builder allocates instance ids under XDG_RUNTIME_DIR; give it a
# writable one when the session's own directory is not writable.
if [ ! -w "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" ]; then
  export XDG_RUNTIME_DIR="$ROOT/build/xdg-runtime"
  mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
fi
export XDG_DATA_HOME="${FLATPAK_DATA_HOME:-$ROOT/.xdg-data}"
export XDG_CACHE_HOME="${FLATPAK_CACHE_HOME:-$ROOT/.xdg-cache}"
export XDG_CONFIG_HOME="${FLATPAK_CONFIG_HOME:-$ROOT/.xdg-config}"
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$OUT"

log() { printf '\033[1;34m[flatpak]\033[0m %s\n' "$*"; }

# --- SDK/runtime --------------------------------------------------------------
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo >/dev/null 2>&1 || true
if ! flatpak --user info org.gnome.Sdk//50 >/dev/null 2>&1; then
  log "installing org.gnome.Sdk//50 + org.gnome.Platform//50 (large download)…"
  flatpak install --user -y flathub org.gnome.Platform//50 org.gnome.Sdk//50
fi

# --- sources -----------------------------------------------------------------
log "preparing sources…"
mkdir -p "$SRC_DIR"
for piece in dsh_linux assets patches vendor bin; do
  tar -czf "$SRC_DIR/$piece.tar.gz" -C "$ROOT" "$piece"
done
tar -czf "$SRC_DIR/snapshot.tar.gz" -C "$ROOT/build" snapshot
cp -f "$ROOT/build/node.tar.xz" "$SRC_DIR/node.tar.xz" 2>/dev/null || true
cp -f "$ROOT/packaging/com.deepseek.dshlinux.desktop" "$SRC_DIR/"
cp -f "$ROOT/packaging/flatpak/bundle-git.sh" "$SRC_DIR/"
cp -f "$ROOT/packaging/flatpak/bundle-pygobject.sh" "$SRC_DIR/"

python3 - "$ROOT" "$SRC_DIR" <<'PY'
import hashlib
import json
import os
import sys

root, src = sys.argv[1], sys.argv[2]


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


manifest = {
    'app-id': 'com.deepseek.dshlinux',
    'runtime': 'org.gnome.Platform',
    'runtime-version': '50',
    'sdk': 'org.gnome.Sdk',
    'command': 'dsh-linux',
    'finish-args': [
        '--socket=x11',
        '--socket=wayland',
        '--socket=fallback-x11',
        '--share=network',
        '--share=ipc',
        '--device=dri',
        '--own-name=com.deepseek.dsh-linux',
        '--own-name=org.kde.StatusNotifierItem.*',
        '--talk-name=org.kde.StatusNotifierWatcher',
        '--talk-name=org.freedesktop.StatusNotifierWatcher',
        '--filesystem=~/.dsh:create',
        '--env=DSH_HOME=~/.dsh',
    ],
    'cleanup': ['/share/doc', '/lib/git-core/*.html', '/lib/git-core/*.txt'],
    'modules': [
        {
            'name': 'nodejs',
            'buildsystem': 'simple',
            'sources': [{'type': 'archive', 'path': 'node.tar.xz', 'sha256': sha(os.path.join(src, 'node.tar.xz'))}],
            'build-commands': [
                'mkdir -p /app/bin /app/lib /app/include',
                'cp -a include/node /app/include/node',
                'cp -a bin/node /app/bin/node',
                'ln -sf node /app/bin/nodejs',
                'cp -a lib/node_modules /app/lib/',
                'rm -rf bin include lib share *.md LICENSE',
            ],
        },
        {
            'name': 'git',
            'buildsystem': 'simple',
            'sources': [{'type': 'file', 'path': 'bundle-git.sh', 'sha256': sha(os.path.join(src, 'bundle-git.sh'))}],
            'build-commands': ['sh bundle-git.sh'],
        },
        {
            'name': 'pygobject',
            'buildsystem': 'simple',
            'sources': [{'type': 'file', 'path': 'bundle-pygobject.sh', 'sha256': sha(os.path.join(src, 'bundle-pygobject.sh'))}],
            'build-commands': ['sh bundle-pygobject.sh'],
        },
        {
            'name': 'dsh-linux',
            'buildsystem': 'simple',
            'sources': [
                {'type': 'file', 'path': 'dsh_linux.tar.gz', 'sha256': sha(os.path.join(src, 'dsh_linux.tar.gz'))},
                {'type': 'file', 'path': 'assets.tar.gz', 'sha256': sha(os.path.join(src, 'assets.tar.gz'))},
                {'type': 'file', 'path': 'patches.tar.gz', 'sha256': sha(os.path.join(src, 'patches.tar.gz'))},
                {'type': 'file', 'path': 'vendor.tar.gz', 'sha256': sha(os.path.join(src, 'vendor.tar.gz'))},
                {'type': 'file', 'path': 'bin.tar.gz', 'sha256': sha(os.path.join(src, 'bin.tar.gz'))},
                {'type': 'file', 'path': 'snapshot.tar.gz', 'sha256': sha(os.path.join(src, 'snapshot.tar.gz'))},
                {'type': 'file', 'path': 'com.deepseek.dshlinux.desktop',
                 'sha256': sha(os.path.join(src, 'com.deepseek.dshlinux.desktop'))},
            ],
            'build-commands': [
                'mkdir -p /app/lib/dsh-linux',
                'tar -xzf dsh_linux.tar.gz -C /app/lib/dsh-linux',
                'tar -xzf assets.tar.gz -C /app/lib/dsh-linux',
                'tar -xzf patches.tar.gz -C /app/lib/dsh-linux',
                'tar -xzf vendor.tar.gz -C /app/lib/dsh-linux',
                'tar -xzf bin.tar.gz -C /app/lib/dsh-linux',
                'mkdir -p /app/share/dsh-linux/dsh /app/bin /app/share/applications',
                'tar -xzf snapshot.tar.gz --strip-components=1 -C /app/share/dsh-linux/dsh',
                'ln -sf ../lib/dsh-linux/bin/dsh-linux /app/bin/dsh-linux',
                'install -Dm644 com.deepseek.dshlinux.desktop /app/share/applications/com.deepseek.dshlinux.desktop',
                'for s in 22 24 48 128 256 512; do mkdir -p /app/share/icons/hicolor/${s}x${s}/apps; '
                'cp /app/lib/dsh-linux/assets/icon-coloured-${s}.png /app/share/icons/hicolor/${s}x${s}/apps/com.deepseek.dshlinux.png; done',
                'mkdir -p /app/share/icons/hicolor/scalable/apps',
                'cp /app/lib/dsh-linux/assets/icon-coloured-48.png /app/share/icons/hicolor/scalable/apps/com.deepseek.dshlinux.png',
            ],
        },
    ],
}
with open(os.path.join(src, 'com.deepseek.dshlinux.json'), 'w') as handle:
    json.dump(manifest, handle, indent=2)
print('manifest written')
PY

# --- build --------------------------------------------------------------------
log "building…"
rm -rf "$BUILD_DIR" "$ROOT/.flatpak-builder" "$ROOT/build/flatpak-repo" "$ROOT/build/flatpak-state"
flatpak-builder --user --force-clean --disable-rofiles-fuse \
  --state-dir="$ROOT/build/flatpak-state" \
  --install-deps-from=flathub "$BUILD_DIR" "$SRC_DIR/com.deepseek.dshlinux.json"

log "exporting repo and bundling…"
flatpak build-export "$ROOT/build/flatpak-repo" "$BUILD_DIR"
flatpak build-bundle "$ROOT/build/flatpak-repo" "$OUT/deepseek-harness-linux-${VERSION}-x86_64.flatpak" "$APP_ID"
ls -la "$OUT/deepseek-harness-linux-${VERSION}-x86_64.flatpak"
log "flatpak bundle done"
