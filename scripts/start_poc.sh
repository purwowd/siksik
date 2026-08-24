#!/usr/bin/env bash
# Start SATRIA PoC (API FastAPI + UI Vite). Mesin akuisisi = SIKSIK/main.
#
# Mac: bash scripts/maconly.sh  (env di repo-root .env)
# Lab tanpa GPU: SADT_OCR_ENABLED=1 EasyOCR CPU; stack Qwen/ICM/SafeWatch/Whisper/CLIP OFF.
# Server GPU: SADT_GPU_STACK_ENABLED=1 (+ plugin flags) sebelum start.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# WSL2: libcuda hidup di /usr/lib/wsl/lib — tanpa ini torch/paddle tidak melihat GPU
if [[ -d /usr/lib/wsl/lib ]]; then
  export PATH="/usr/lib/wsl/lib:${PATH:-}"
  export LD_LIBRARY_PATH="/usr/lib/wsl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
# Prefer ~/bin adb wrapper (USB via Windows ADB server) + local Android SDK tools
export PATH="${HOME}/bin:${HOME}/Android/Sdk/platform-tools:${HOME}/Android/Sdk/cmdline-tools/latest/bin:${PATH:-}"
# `ip` hanya ada di Linux/WSL. Di Mac perintah itu 127 + pipefail = skrip diam-diam exit.
WSL_ADB_GATEWAY=""
if command -v ip >/dev/null 2>&1; then
  WSL_ADB_GATEWAY="$(ip route show default 2>/dev/null | awk '{print $3}' || true)"
fi
if [[ -n "${WSL_ADB_GATEWAY:-}" ]]; then
  export ADB_SERVER_SOCKET="tcp:${WSL_ADB_GATEWAY}:5037"
  export SADT_AGENT_FORWARD_HOST="${SADT_AGENT_FORWARD_HOST:-$WSL_ADB_GATEWAY}"
fi
if [[ -d /usr/lib/jvm/java-17-openjdk-amd64 ]]; then
  export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
fi
export ANDROID_HOME="${ANDROID_HOME:-${HOME}/Android/Sdk}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"

# Load repo-root .env then backend/.env (only keys not already set by caller).
# Without this, the lab defaults below would export 0 and override pydantic .env.
load_env_file() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      val="${val#\"}"; val="${val%\"}"
      val="${val#\'}"; val="${val%\'}"
      if [[ -z "${!key+x}" ]]; then
        export "$key=$val"
      fi
    fi
  done < "$env_file"
}
load_env_file "$ROOT/.env"
load_env_file "$ROOT/backend/.env"

API_PORT="${SADT_API_PORT:-8000}"
UI_PORT="${SADT_UI_PORT:-5173}"
# WSL: bind all interfaces for Windows portproxy. Mac lab: loopback (lihat .env).
if [[ "$(uname -s)" == "Darwin" ]]; then
  API_HOST="${SADT_API_HOST:-127.0.0.1}"
  UI_BIND="${SADT_UI_BIND:-127.0.0.1}"
else
  API_HOST="${SADT_API_HOST:-0.0.0.0}"
  UI_BIND="${SADT_UI_BIND:-0.0.0.0}"
fi
export SADT_API_HOST="$API_HOST"
export SADT_API_PORT="$API_PORT"
export SADT_UI_PORT="$UI_PORT"
# Vite SATRIA membaca SATRIA_API_PORT / SATRIA_UI_PORT
export SATRIA_API_PORT="${SATRIA_API_PORT:-$API_PORT}"
export SATRIA_UI_PORT="${SATRIA_UI_PORT:-$UI_PORT}"

# ---- Lab-safe defaults (hanya jika belum di-set caller / .env) ----
: "${SADT_OCR_ENABLED:=1}"
: "${SADT_OCR_BACKEND:=easyocr}"
: "${SADT_OCR_GPU:=0}"
: "${SADT_GPU_STACK_ENABLED:=0}"
: "${SADT_GPU_WHISPER_ENABLED:=0}"
: "${SADT_GPU_SAFEWATCH_ENABLED:=0}"
: "${SADT_GPU_ICM_ENABLED:=0}"
: "${SADT_GPU_QWEN_ENABLED:=0}"
: "${SADT_CLIP_TOKOH_ENABLED:=0}"
# media_text = jalur enrichment ekstra (bisa tarik EasyOCR/Whisper). Lab: matikan;
# pakai SADT_OCR_ENABLED=1 untuk image-to-text eksplisit.
: "${SADT_MEDIA_TEXT_ENABLED:=0}"
: "${SADT_NUDITY_DETECTION_ENABLED:=1}"

export SADT_OCR_ENABLED SADT_OCR_BACKEND SADT_OCR_GPU \
  SADT_GPU_STACK_ENABLED SADT_GPU_WHISPER_ENABLED \
  SADT_GPU_SAFEWATCH_ENABLED SADT_GPU_ICM_ENABLED SADT_GPU_QWEN_ENABLED \
  SADT_CLIP_TOKOH_ENABLED SADT_MEDIA_TEXT_ENABLED SADT_NUDITY_DETECTION_ENABLED

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
# pip -r requirements.txt di Mac = torch/easyocr, menit. Skip jika venv sudah jalan.
if [[ "${SADT_PIP_INSTALL:-0}" == "1" ]] || ! python -c "import uvicorn, fastapi" >/dev/null 2>&1; then
  echo "Installing Python deps (set SADT_PIP_INSTALL=1 to force)…"
  pip install -q -r requirements.txt
fi

echo "Starting API on $API_HOST:$API_PORT (auto-restart on crash)"
echo "  OCR (image→text): ${SADT_OCR_ENABLED:-0}  backend=${SADT_OCR_BACKEND:-default}  gpu=${SADT_OCR_GPU:-0}"
echo "  workers:          ${SADT_WORKER_CONCURRENCY:-4}"
echo "  media_text:       ${SADT_MEDIA_TEXT_ENABLED}"
echo "  nudity detector:  ${SADT_NUDITY_DETECTION_ENABLED}"
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
# Non-interactive shells (nohup/wsl -e) often miss nvm PATH
if ! command -v npx >/dev/null 2>&1; then
  if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
    # shellcheck disable=SC1090
    source "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  fi
fi
npx vite --host "$UI_BIND" --port "$UI_PORT" &
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
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "SATRIA // OPS (Mac)"
  echo "  API  http://127.0.0.1:$API_PORT/docs"
  echo "  UI   http://127.0.0.1:$UI_PORT"
  echo "  Ctrl+C to stop"
else
  WSL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "SATRIA // OPS"
  echo "  bind API $API_HOST:$API_PORT   UI $UI_BIND:$UI_PORT"
  echo "  WSL   UI   http://127.0.0.1:$UI_PORT"
  echo "  WSL   API  http://127.0.0.1:$API_PORT/docs"
  if [[ -n "${WSL_IP:-}" ]]; then
    echo "  WSL   direct http://$WSL_IP:$UI_PORT"
  fi
  echo ""
  echo "  Windows browser: http://127.0.0.1:$UI_PORT"
  echo "  (NAT localhostForwarding — jangan pakai portproxy/expose_lan lama)"
  echo "  Jika 127.0.0.1 gagal (Admin):"
  echo "    powershell -ExecutionPolicy Bypass -File C:\\siksik\\scripts\\expose_lan.ps1"
  echo "  Ctrl+C to stop"
fi
wait
