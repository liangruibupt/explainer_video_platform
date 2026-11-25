#!/usr/bin/env bash
# Preview stills of one GLB on this CPU box (Remotion + @remotion/three, --gl=swangle):
#   render.sh MODEL.glb OUT.jpg [clay|tex] [AZ ...]
# Renders one 640x640 still per azimuth (default 35 and 215 degrees) and joins them side by
# side into OUT.jpg (quality 4, a few tens of KB). Needs infra/previs/setup.sh run once.
set -euo pipefail
cd "$(dirname "$0")"
GLB=$1; OUT=$2; MODE=${3:-clay}; shift $(( $# >= 3 ? 3 : $# )); AZS=${*:-35 215}
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
FF="$TOOLS/ffmpeg"
ln -sfn "$TOOLS/remotion-base/node_modules" node_modules
mkdir -p public out
cp -f "$GLB" public/model.glb   # a copy: Remotion's static server refuses symlinks
CLAY=$([ "$MODE" = clay ] && echo true || echo false)
ins=()
for az in $AZS; do
  node_modules/.bin/remotion still src/index.ts Preview "out/az$az.png" --gl=swangle --log=error \
    --props="{\"glb\":\"model.glb\",\"az\":$az,\"clay\":$CLAY}"
  ins+=(-i "out/az$az.png")
done
n=$(echo $AZS | wc -w)
if [ "$n" -gt 1 ]; then F="hstack=inputs=$n"; else F="null"; fi
"$FF" -v error -y "${ins[@]}" -filter_complex "$F" -q:v 4 "$OUT"
ls -l "$OUT"
