#!/usr/bin/env bash
# Install the 3D previs packages into the shared Remotion install ($EVP_TOOLS/remotion-base),
# so every job project (node_modules -> remotion-base/node_modules) can import three.js,
# @react-three/fiber and @remotion/three. Additive and idempotent; exact versions.
# Each part lists its own packages in infra/previs/packages.<part>.txt (one "name@version"
# per line, # comments allowed), so parallel parts never edit the same file.
# Usage: infra/previs/setup.sh   (run once per machine, after the Remotion base exists)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
BASE="$TOOLS/remotion-base"
if [ ! -f "$BASE/package.json" ]; then
  echo "no Remotion base at $BASE (see docs/REMOTION_CONTRACT.md)" >&2
  exit 1
fi
mapfile -t PKGS < <(cat "$HERE"/packages.*.txt | sed 's/#.*//' | tr -s ' \t' '\n' | grep -v '^$' | sort -u)
echo "installing into $BASE: ${PKGS[*]}"
cd "$BASE"
npm install --no-audit --no-fund --save-exact "${PKGS[@]}"
for p in three @react-three/fiber @remotion/three; do
  node -e "console.log(process.argv[1], JSON.parse(require('fs').readFileSync('node_modules/' + process.argv[1] + '/package.json')).version)" "$p"
done
