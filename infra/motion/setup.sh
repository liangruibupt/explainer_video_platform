#!/usr/bin/env bash
# Prepare the vendored shotcraft Motion Workbench for the studio's motion editor
# (docs/REMOTION_CONTRACT.md §7). Idempotent: safe to run again at any time.
#
#   infra/motion/setup.sh            apply patches, npm install, gen-index
#   infra/motion/setup.sh --media    also fetch the gallery preview MP4s (needs gh; large download)
#
# The checkout itself ($EVP_TOOLS/shotcraft, pinned at 3ac67b7) is only changed through the
# patch files next to this script.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
SC="${EVP_SHOTCRAFT:-$TOOLS/shotcraft}"
WB="$SC/workbench"
PIN=3ac67b7

[ -d "$WB" ] || { echo "workbench not found: $WB" >&2; exit 2; }
head="$(git -C "$SC" rev-parse --short=7 HEAD)"
[ "$head" = "$PIN" ] || echo "warning: shotcraft checkout is at $head, patches were made for $PIN" >&2

for p in "$HERE"/patches/*.patch; do
  name="$(basename "$p")"
  if git -C "$SC" apply --reverse --check "$p" 2>/dev/null; then
    echo "patch $name: already applied"
  elif git -C "$SC" apply --check "$p"; then
    git -C "$SC" apply "$p"
    echo "patch $name: applied"
  else
    echo "patch $name: does not apply to $SC (local changes?)" >&2
    exit 1
  fi
done

if [ ! -d "$WB/node_modules/@remotion/cli" ]; then
  echo "installing workbench dependencies..."
  (cd "$WB" && npm ci --ignore-scripts --no-audit --no-fund)
fi
(cd "$WB" && node scripts/gen-index.mjs)

if [ "${1:-}" = "--media" ]; then
  (cd "$SC/gallery" && bash fetch-media.sh)
fi
echo "motion workbench ready: $WB"
