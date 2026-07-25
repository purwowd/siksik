#!/usr/bin/env bash
# Start SADT PoC (API + UI)
#
# Mac lab (tanpa perangkat AI): image-to-text = SADT_OCR_ENABLED=1
# Stack AI (Qwen/ICM/SafeWatch/Whisper/CLIP) default OFF di sini.
# Server GPU: set SADT_GPU_STACK_ENABLED=1 (+ plugin flags) sebelum start.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

API_PORT="${SADT_API_PORT:-8000}"
UI_PORT="${SADT_UI_PORT:-5173}"
# Default loopback only; set SADT_API_HOST=0.0.0.0 untuk expose LAN (lab saja)
API_HOST="${SADT_API_HOST:-127.0.0.1}"

# ---- Lab-safe defaults (hanya jika belum di-set caller) ----
: "${SADT_GPU_STACK_ENABLED:=0}"
: "${SADT_GPU_WHISPER_ENABLED:=0}"
: "${SADT_GPU_SAFEWATCH_ENABLED:=0}"
: "${SADT_GPU_ICM_ENABLED:=0}"
: "${SADT_GPU_QWEN_ENABLED:=0}"
: "${SADT_CLIP_TOKOH_ENABLED:=0}"
# media_text = jalur enrichment ekstra (bisa tarik EasyOCR/Whisper). Lab: matikan;
# pakai SADT_OCR_ENABLED=1 untuk image-to-text eksplisit.
: "${SADT_MEDIA_TEXT_ENABLED:=0}"

export SADT_GPU_STACK_ENABLED SADT_GPU_WHISPER_ENABLED \
  SADT_GPU_SAFEWATCH_ENABLED SADT_GPU_ICM_ENABLED SADT_GPU_QWEN_ENABLED \
  SADT_CLIP_TOKOH_ENABLED SADT_MEDIA_TEXT_ENABLED

# EasyOCR CPU + worker_concurrency=4 OOM Mac (uvicorn Killed:9). When GPU is off,
# serialize analysis OCR and shrink preprocess unless caller overrides.
if [[ "${SADT_OCR_GPU:-0}" != "1" ]]; then
  : "${SADT_WORKER_CONCURRENCY:=1}"
  : "${SADT_OCR_MAX_EDGE_PX:=1280}"
  : "${SADT_OCR_MIN_EDGE_PX:=0}"
  : "${SADT_OCR_MAG_RATIO:=1.5}"
  : "${SADT_ANDROID_SOCIAL_OCR_MAX_EDGE_PX:=960}"
  : "${SADT_ANDROID_SOCIAL_OCR_MAG_RATIO:=1.15}"
  export SADT_WORKER_CONCURRENCY \
    SADT_OCR_MAX_EDGE_PX SADT_OCR_MIN_EDGE_PX SADT_OCR_MAG_RATIO \
    SADT_ANDROID_SOCIAL_OCR_MAX_EDGE_PX SADT_ANDROID_SOCIAL_OCR_MAG_RATIO
fi

# Free API port if leftover process
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -ti:"$API_PORT" || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "Stopping old process on :$API_PORT → $PIDS"
    # shellcheck disable=SC2086
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

echo "Starting API on $API_HOST:$API_PORT (auto-restart on crash)"
echo "  OCR (image→text): ${SADT_OCR_ENABLED:-0}  backend=${SADT_OCR_BACKEND:-default}  gpu=${SADT_OCR_GPU:-0}"
echo "  workers:          ${SADT_WORKER_CONCURRENCY:-4}"
echo "  media_text:       ${SADT_MEDIA_TEXT_ENABLED}"
echo "  GPU AI stack:     ${SADT_GPU_STACK_ENABLED}  (whisper=${SADT_GPU_WHISPER_ENABLED} icm=${SADT_GPU_ICM_ENABLED} qwen=${SADT_GPU_QWEN_ENABLED} clip=${SADT_CLIP_TOKOH_ENABLED})"

# Watchdog: if uvicorn dies (OOM/Killed:9), bring it back without killing Vite.
API_WATCHDOG_PID=""
(
  while true; do
    uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" --workers 1
    ec=$?
    echo "API exited (code=$ec) — restarting in 3s…"
    sleep 3
  done
) &
API_WATCHDOG_PID=$!

cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then
  npm install
fi

# Point Vite proxy to chosen API port
export SADT_API_PORT
npx vite --host 127.0.0.1 --port "$UI_PORT" &
UI_PID=$!

cleanup() {
  kill "$UI_PID" 2>/dev/null || true
  if [[ -n "${API_WATCHDOG_PID}" ]]; then
    kill "$API_WATCHDOG_PID" 2>/dev/null || true
    # Also stop the child uvicorn if still bound.
    if command -v lsof >/dev/null 2>&1; then
      PIDS="$(lsof -ti:"$API_PORT" || true)"
      if [[ -n "${PIDS}" ]]; then
        # shellcheck disable=SC2086
        kill $PIDS 2>/dev/null || true
      fi
    fi
  fi
}
trap cleanup EXIT INT TERM

echo ""
echo "SADT // OPS"
echo "  API  http://127.0.0.1:$API_PORT/docs"
echo "  UI   http://127.0.0.1:$UI_PORT"
echo "  Ctrl+C to stop"
wait
