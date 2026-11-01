#!/usr/bin/env bash
# Build the portable .tar.gz for generic Linux distributions: a self-contained
# tree that runs in place (or installs per-user with ./install.sh) on any
# distro that provides python3 + PyGObject + GTK4 + WebKitGTK 6.0 + git.
# Node.js >= 24.9 is resolved on the host at startup (nvm/volta/asdf/mise/fnm
# installs are auto-discovered); pnpm and a C compiler are bundled.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="deepseek-harness-linux"
VERSION="0.1.0"
TAR_ROOT="$ROOT/build/tar/$NAME"
OUT="$ROOT/dist/$NAME-$VERSION-x86_64.tar.gz"

log() { printf '\033[1;34m[tar]\033[0m %s\n' "$*"; }

log "assembling the portable tree…"
rm -rf "$ROOT/build/tar"
mkdir -p "$TAR_ROOT/share/dsh-linux"

cp -a "$ROOT/dsh_linux" "$ROOT/assets" "$ROOT/vendor" "$ROOT/patches" "$ROOT/bin" "$TAR_ROOT/"
cp -a "$ROOT/install.sh" "$TAR_ROOT/install.sh"

log "bundling the DeepSeek Harness snapshot…"
if [ ! -d "$ROOT/build/snapshot/.git" ]; then
  log "snapshot missing; run packaging/make-snapshot.sh first"
  exit 1
fi
cp -a "$ROOT/build/snapshot" "$TAR_ROOT/share/dsh-linux/dsh"

log "writing $OUT"
tar -czf "$OUT" -C "$ROOT/build/tar" "$NAME"
du -sh "$OUT"
log "tarball ready"
