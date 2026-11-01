#!/usr/bin/env bash
# Rebuild the bundled DeepSeek Harness snapshot (git history + tracked tree,
# no node_modules/build outputs) used by every package format. Clones the
# upstream checkout from third_party/dsh, which is git-ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PATCH_BASE="f3803c96974ad4a94195fb97e5c637e9c2a3afe3"
PATCH_TIP="01e509e4b7b77bcc57a27d244d0225ed693bcd55"

rm -rf build/snapshot
mkdir -p build
git clone --quiet third_party/dsh build/snapshot
git -C build/snapshot remote set-url origin https://github.com/deepseek-ai/deepseek-harness.git
git -C build/snapshot update-ref refs/desktop/patch-base "$PATCH_BASE"
git -C build/snapshot update-ref refs/desktop/patch-tip "$PATCH_TIP"
git -C build/snapshot gc --aggressive --prune=now --quiet
echo "snapshot ready:"
du -sh build/snapshot
