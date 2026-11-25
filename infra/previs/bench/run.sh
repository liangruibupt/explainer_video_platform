#!/usr/bin/env bash
# Greybox render benchmark: 150 frames (5 s @ 30 fps) of an engine-like set, camera dolly+orbit.
# Prints wall time (bundling included, about 5 s) and frames per second for each case.
# Usage: infra/previs/bench/run.sh        (after infra/previs/setup.sh)
set -uo pipefail
cd "$(dirname "$0")"
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
ln -sfn "$TOOLS/remotion-base/node_modules" node_modules
mkdir -p out
RM=node_modules/.bin/remotion
case_() { # comp name concurrency props
  local s e rc
  s=$(date +%s.%N)
  "$RM" render src/index.ts "$1" "out/$2.mp4" --gl=swangle --concurrency="$3" --props="$4" --log=error > "out/$2.log" 2>&1
  rc=$?
  e=$(date +%s.%N)
  awk -v n="$2" -v rc="$rc" -v t="$(echo "$e - $s" | bc)" 'BEGIN{printf "%-14s rc=%s %6.1f s  %5.2f fps\n", n, rc, t, 150/t}'
}
case_ Bench720  720-1x-c2   2 '{"shadows":true,"copies":1}'
case_ Bench1080 1080-1x-c2  2 '{"shadows":true,"copies":1}'
case_ Bench1080 1080-1x-ns  2 '{"shadows":false,"copies":1}'
case_ Bench1080 1080-4x-c2  2 '{"shadows":true,"copies":4}'
case_ Bench1080 1080-4x-c4  4 '{"shadows":true,"copies":4}'
case_ Bench1080 1080-16x-c2 2 '{"shadows":true,"copies":16}'
