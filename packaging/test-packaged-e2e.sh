#!/usr/bin/env bash
# Packaged-mode E2E: extract the .deb into a fake root, launch it like a
# desktop session (minimal PATH, fresh HOME), and verify the first-run flow
# (snapshot copy -> pnpm install -> build -> boot).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

rm -rf build/fake-root build/fake-home
mkdir -p build/fake-root build/fake-home
dpkg-deb -x dist/deepseek-harness-linux_0.1.0-1_amd64.deb build/fake-root/

# Fast path for dependencies: reuse the workspace pnpm store + npm cache.
mkdir -p build/fake-home/.config/dsh-linux
ln -sfn "$ROOT/.pnpm-store" build/fake-home/.config/dsh-linux/pnpm-store
ln -sfn "$ROOT/.npm-cache" build/fake-home/.config/dsh-linux/npm-cache

# Desktop-like launch: the session environment is inherited (D-Bus, display,
# locale…), only the PATH is trimmed and the writable state lands in the
# fake home, mirroring how a .desktop launch behaves on a real desktop.
env \
  HOME="$ROOT/build/fake-home" \
  PATH=/usr/bin:/bin \
  XDG_DATA_HOME="$ROOT/build/fake-home/.local/share" \
  XDG_CONFIG_HOME="$ROOT/build/fake-home/.config" \
  DSH_LINUX_APP_ID="com.deepseek.dsh-linux.e2ev3" \
  DSH_LINUX_NODE="$(command -v node)" \
  DSH_LINUX_DSH_HOME="$ROOT/build/fake-home/.dsh" \
  DSH_LINUX_PORT=3100 \
  build/fake-root/usr/bin/dsh-linux > build/packaged-e2e.log 2>&1

echo "exit=$?"
grep -E "dsh-linux:" build/packaged-e2e.log | head -20
echo "=== provisioned repo ==="
ls build/fake-home/.local/share/dsh-linux/dsh/.git/HEAD 2>/dev/null && echo "repo copied"
ls build/fake-home/.local/share/dsh-linux/dsh/apps/cli/lib/bin.js 2>/dev/null && echo "built artifacts present"
