#!/bin/sh
# Bundle the SDK's git plus its non-runtime shared libraries into /app so the
# sandboxed app can fetch/rebuild itself (the platform runtime has no git).
set -e
mkdir -p /app/bin /app/lib/git-core
# /usr/bin/git is a symlink into git-core in the SDK; copy the real binary.
GIT_REAL="$(readlink -f /usr/bin/git)"
cp -a "$GIT_REAL" /app/bin/git
chmod +x /app/bin/git
cp -a /usr/libexec/git-core/. /app/lib/git-core/
for bin in /app/bin/git /app/lib/git-core/*; do
  [ -f "$bin" ] || continue
  for lib in $(ldd "$bin" 2>/dev/null | awk '/=> \//{print $3}'); do
    case "$lib" in
      /usr/lib/x86_64-linux-gnu/libc.so*|/usr/lib64/libc.so*|/lib64/ld-linux*|/usr/lib/x86_64-linux-gnu/ld-linux*)
        ;;
      *)
        cp -n "$lib" /app/lib/ 2>/dev/null || true
        ;;
    esac
  done
done
echo "bundled git: $(/app/bin/git --version)"
