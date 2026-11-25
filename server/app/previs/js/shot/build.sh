#!/usr/bin/env bash
# Bundle part B's commands (shot/main.ts) to js/dist/shot.mjs with esbuild from the shared
# Remotion base. server/app/previs/shots.py runs this when the bundle is missing or older
# than the sources.
set -euo pipefail
cd "$(dirname "$0")/.."
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
NM="$TOOLS/remotion-base/node_modules"
ln -sfn "$NM" node_modules
mkdir -p dist
"$NM/.bin/esbuild" shot/main.ts --bundle --platform=node --format=esm --log-level=warning --outfile=dist/shot.mjs
