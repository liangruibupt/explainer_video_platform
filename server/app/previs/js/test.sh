#!/usr/bin/env bash
# Run every *.test.ts under server/app/previs/js with esbuild from the shared Remotion base.
# Exit code = first failing test file's exit code.
set -euo pipefail
cd "$(dirname "$0")"
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
NM="$TOOLS/remotion-base/node_modules"
ln -sfn "$NM" node_modules   # gitignored, same as a job project's node_modules link
OUT="$(mktemp -d)"
trap 'rm -r "$OUT"' EXIT
for f in $(find . -name '*.test.ts' -not -path './node_modules/*' | sort); do
  echo "== $f"
  "$NM/.bin/esbuild" "$f" --bundle --platform=node --format=esm --log-level=warning --outfile="$OUT/t.mjs"
  node "$OUT/t.mjs"
done
