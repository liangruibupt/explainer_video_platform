#!/usr/bin/env bash
# Start or stop the local studio (API + built web UI) on 127.0.0.1.
# Usage: scripts/studio.sh start|stop|status   (PORT defaults to 8765)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8765}"
TOOLS="${EVP_TOOLS:-$HOME/.cache/explainer-video}"
PY="${EVP_PYTHON:-$TOOLS/venv/bin/python}"
DATA="$ROOT/data"
PIDFILE="$DATA/server.pid"
mkdir -p "$DATA"

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-status}" in
  start)
    if running; then echo "already running (pid $(cat "$PIDFILE")) on http://127.0.0.1:$PORT"; exit 0; fi
    [ -d "$ROOT/web/dist" ] || (cd "$ROOT/web" && npm install --no-audit --no-fund && npm run build)
    cd "$ROOT/server"
    # setsid: own session, so the server survives the shell (or agent session) that started it.
    setsid nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
      >> "$DATA/server.log" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    for _ in $(seq 1 20); do
      curl -fs -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
      sleep 0.5
    done
    echo "started (pid $(cat "$PIDFILE")) on http://127.0.0.1:$PORT"
    ;;
  stop)
    if running; then kill "$(cat "$PIDFILE")"; echo "stopped pid $(cat "$PIDFILE")"; else echo "not running"; fi
    rm -f "$PIDFILE"
    ;;
  status)
    if running; then echo "running (pid $(cat "$PIDFILE")) on http://127.0.0.1:$PORT"; else echo "not running"; fi
    ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
