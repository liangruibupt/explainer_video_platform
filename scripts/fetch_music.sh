#!/usr/bin/env bash
# Download the background-music tracks listed in music/library.json into the tools cache.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${EVP_TOOLS:-$HOME/.cache/explainer-video}/music"
mkdir -p "$DEST"
python3 - "$ROOT/music/library.json" <<'PY' | while IFS=$'\t' read -r file url; do
import json, sys
for k, v in json.load(open(sys.argv[1])).items():
    if not k.startswith('_'):
        print(f"{v['file']}\t{v['url']}")
PY
  if [ -s "$DEST/$file" ]; then echo "have $file"; else
    echo "get  $file"; curl -fsSL -o "$DEST/$file.part" "$url" && mv "$DEST/$file.part" "$DEST/$file"; fi
done
