#!/usr/bin/env bash
# Bundle cli.ts into dist/previs.mjs (gitignored) with esbuild from the shared Remotion base.
# server/app/previs/assets_build.py runs this when dist/previs.mjs is missing or older than
# any source file. Remotion packages are not bundled: the turntable command calls the
# remotion CLI, which bundles asset/turntable/ itself.
set -euo pipefail
cd "$(dirname "$0")"
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
NM="${EVP_REMOTION_BASE:-$TOOLS/remotion-base}/node_modules"
ln -sfn "$NM" node_modules   # gitignored; also lets the remotion bundler resolve three etc.
mkdir -p dist
"$NM/.bin/esbuild" cli.ts --bundle --platform=node --format=esm --target=node20 \
  --log-level=warning --outfile=dist/previs.mjs.tmp
mv dist/previs.mjs.tmp dist/previs.mjs
