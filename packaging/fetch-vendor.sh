#!/usr/bin/env bash
# Provision everything under vendor/ that the packages bundle:
#   - pnpm (pinned, from the npm registry)
#   - the bundled C compiler wrapper (tcc) used for first-run builds/updates
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash packaging/fetch-pnpm.sh
bash packaging/vendor-cc.sh
echo "vendor/ ready"
