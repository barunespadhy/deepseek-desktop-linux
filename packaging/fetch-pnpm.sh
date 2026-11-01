#!/usr/bin/env bash
# Fetch the pinned pnpm release (matching dsh's `packageManager` field) from
# the npm registry into vendor/pnpm, so package builds do not depend on a
# system pnpm. At runtime the updater falls back to corepack when this is
# absent; this just makes first-run faster and offline-friendly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(grep -o '"packageManager": "pnpm@[^"]*"' dsh/package.json 2>/dev/null \
  | head -1 | sed 's/.*pnpm@\([^"]*\)"/\1/' || true)"
VERSION="${VERSION:-11.28.5}"

log() { printf '\033[1;34m[pnpm]\033[0m %s\n' "$*"; }

log "fetching pnpm $VERSION…"
rm -rf build/pnpm-dl vendor/pnpm
mkdir -p build/pnpm-dl vendor
curl -fsSL "https://registry.npmjs.org/pnpm/-/pnpm-${VERSION}.tgz" -o build/pnpm-dl/pnpm.tgz
tar -xzf build/pnpm-dl/pnpm.tgz -C build/pnpm-dl
mv build/pnpm-dl/package vendor/pnpm
rm -rf build/pnpm-dl
node vendor/pnpm/bin/pnpm.mjs --version >/dev/null 2>&1 \
  || echo "warning: could not verify with system node; the app will verify at startup"
log "vendor/pnpm ready ($(du -sh vendor/pnpm | cut -f1))"
