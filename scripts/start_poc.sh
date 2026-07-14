#!/usr/bin/env bash
# Start SADT PoC (API + UI) — lab / server GPU
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

API_PORT="${SADT_API_PORT:-8000}"
UI_PORT="${SADT_UI_PORT:-5173}"
# Default loopback only; set SADT_API_HOST=0.0.0.0 untuk expose LAN (lab saja)
API_HOST="${SADT_API_HOST:-127.0.0.1}"

# Free API port if leftover process
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -ti:"$API_PORT" || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "Stopping old process on :$API_PORT → $PIDS"
    kill -9 $PIDS || true
    sleep 1
  fi
fi

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

echo "Starting API on $API_HOST:$API_PORT"
uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" --workers 1 &
API_PID=$!

cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then
  npm install
fi

# Point Vite proxy to chosen API port
export SADT_API_PORT
npx vite --host 127.0.0.1 --port "$UI_PORT" &
UI_PID=$!

cleanup() {
  kill "$UI_PID" "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo ""
echo "SADT // OPS"
echo "  API  http://127.0.0.1:$API_PORT/docs"
echo "  UI   http://127.0.0.1:$UI_PORT"
echo "  Ctrl+C to stop"
wait
