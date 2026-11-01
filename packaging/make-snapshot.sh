#!/usr/bin/env bash
# Rebuild the bundled DeepSeek Harness snapshot (git history + tracked tree,
# no node_modules/build outputs) used by every package format. Clones the
# upstream checkout from third_party/dsh, which is git-ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The desktop patch range: prefer the refs recorded in the source checkout
# (install.sh / the updater maintain them), and fall back to the commit range
# the bundled patch files imply. Refs must exist in third_party/dsh, so never
# hardcode am-recreated hashes here.
PATCH_BASE="$(git -C third_party/dsh rev-parse --verify refs/desktop/patch-base 2>/dev/null || true)"
PATCH_TIP="$(git -C third_party/dsh rev-parse --verify refs/desktop/patch-tip 2>/dev/null || true)"
if [ -z "$PATCH_BASE" ]; then
  count="$(ls patches/*.patch 2>/dev/null | wc -l)"
  PATCH_BASE="$(git -C third_party/dsh rev-parse "HEAD~$count")"
fi
if [ -z "$PATCH_TIP" ]; then
  PATCH_TIP="$(git -C third_party/dsh rev-parse HEAD)"
fi

rm -rf build/snapshot
mkdir -p build
git clone --quiet third_party/dsh build/snapshot
git -C build/snapshot remote set-url origin https://github.com/deepseek-ai/deepseek-harness.git
git -C build/snapshot update-ref refs/desktop/patch-base "$PATCH_BASE"
git -C build/snapshot update-ref refs/desktop/patch-tip "$PATCH_TIP"
git -C build/snapshot gc --aggressive --prune=now --quiet
echo "snapshot ready:"
du -sh build/snapshot
