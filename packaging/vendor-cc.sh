#!/usr/bin/env bash
# Bundle a self-contained C compiler (tcc) into vendor/cc so first-run builds
# and git updates never depend on the host having gcc/clang. The `cc` wrapper
# adds tcc's -B search path, so any build script invoking plain `cc` works.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

rm -rf build/tcc-dl vendor/cc
mkdir -p build/tcc-dl vendor/cc
cd build/tcc-dl
apt-get download tcc >/dev/null 2>&1 || { echo "apt-get download tcc failed" >&2; exit 1; }
dpkg-deb -x tcc*.deb root/
cd "$ROOT"
mkdir -p vendor/cc/bin vendor/cc/lib
cp build/tcc-dl/root/usr/bin/tcc vendor/cc/bin/tcc
cp -a build/tcc-dl/root/usr/lib/x86_64-linux-gnu/tcc vendor/cc/lib/tcc
# System C headers: stripped runtimes (Flatpak) and dev-tool-free hosts have
# no /usr/include, but the Node-API addons tcc compiles need the freestanding
# subset (stdint/stddef/stdbool and their bits/ companions).
cp -a /usr/include vendor/cc/include
chmod +x vendor/cc/bin/tcc
cat > vendor/cc/bin/cc <<'EOF'
#!/bin/sh
# DeepSeek Harness bundled C compiler wrapper (tcc); relocatable.
# -nostdlib skips glibc's crt object files, which stripped runtimes (like the
# Flatpak platform) do not ship; the Node-API addons built through this
# wrapper do not need them.
DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
exec "$DIR/bin/tcc" -B "$DIR/lib/tcc" -nostdlib -I "$DIR/include" -I "$DIR/include/x86_64-linux-gnu" "$@"
EOF
chmod +x vendor/cc/bin/cc
rm -rf build/tcc-dl
vendor/cc/bin/cc --version 2>/dev/null | head -1 || true
echo "vendor/cc ready"
