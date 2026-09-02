#!/usr/bin/env bash
# Start SATRIA PoC (API FastAPI + UI Vite). Mesin akuisisi = SIKSIK/main.
#
# Mac: bash scripts/maconly.sh  (env di repo-root .env)
# Lab tanpa GPU: SADT_OCR_ENABLED=1 EasyOCR CPU; stack Qwen/ICM/SafeWatch/Whisper/CLIP OFF.
# Server GPU: SADT_GPU_STACK_ENABLED=1 (+ plugin flags) sebelum start.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load repo-root .env then backend/.env (only keys not already set by caller).
# Platform discovery below therefore remains overridable by deployment config.
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

# WSL2: libcuda hidup di /usr/lib/wsl/lib — tanpa ini torch/paddle tidak melihat GPU
if [[ -d /usr/lib/wsl/lib ]]; then
  export PATH="/usr/lib/wsl/lib:${PATH:-}"
  export LD_LIBRARY_PATH="/usr/lib/wsl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# Respect deployment configuration first, then discover conventional SDK roots.
# No host-specific SDK path or ADB transport is forced into the shared process.
if [[ -z "${ANDROID_HOME:-}" && -n "${ANDROID_SDK_ROOT:-}" ]]; then
  export ANDROID_HOME="$ANDROID_SDK_ROOT"
fi
if [[ -z "${ANDROID_HOME:-}" ]]; then
  SDK_CANDIDATES=()
  case "$(uname -s)" in
    Darwin)
      SDK_CANDIDATES+=("${HOME}/Library/Android/sdk")
      ;;
    Linux)
      SDK_CANDIDATES+=(
        "${HOME}/Android/Sdk"
        "${HOME}/Android/sdk"
        "/usr/lib/android-sdk"
      )
      ;;
  esac
  for sdk_candidate in "${SDK_CANDIDATES[@]}"; do
    if [[ -x "${sdk_candidate}/platform-tools/adb" ]]; then
      export ANDROID_HOME="$sdk_candidate"
      break
    fi
  done
fi
if [[ -n "${ANDROID_HOME:-}" ]]; then
  export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"
  export PATH="${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/cmdline-tools/latest/bin:${HOME}/bin:${PATH:-}"
else
  export PATH="${HOME}/bin:${PATH:-}"
fi
if [[ -d /usr/lib/jvm/java-17-openjdk-amd64 ]]; then
  export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
fi

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
: "${SADT_SD_DETECTOR_ENABLED:=0}"

export SADT_OCR_ENABLED SADT_OCR_BACKEND SADT_OCR_GPU \
  SADT_GPU_STACK_ENABLED SADT_GPU_WHISPER_ENABLED \
  SADT_GPU_SAFEWATCH_ENABLED SADT_GPU_ICM_ENABLED SADT_GPU_QWEN_ENABLED \
  SADT_CLIP_TOKOH_ENABLED SADT_MEDIA_TEXT_ENABLED SADT_NUDITY_DETECTION_ENABLED \
  SADT_SD_DETECTOR_ENABLED

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
# onnxruntime-gpu needs Torch's CUDA 12 / cuDNN 9 wheels; WSL only ships the driver.
NVIDIA_CUDA_LIBS="$(
  python -c "
from pathlib import Path
try:
    import nvidia
except ImportError:
    raise SystemExit(0)
root = Path(nvidia.__file__).resolve().parent
print(':'.join(str(path) for path in sorted(root.glob('*/lib')) if path.is_dir()))
" 2>/dev/null || true
)"
if [[ -n "${NVIDIA_CUDA_LIBS}" ]]; then
  export LD_LIBRARY_PATH="${NVIDIA_CUDA_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
# pip -r requirements.txt di Mac = torch/easyocr, menit. Skip jika venv sudah jalan.
if [[ "${SADT_PIP_INSTALL:-0}" == "1" ]] || ! python -c "import uvicorn, fastapi" >/dev/null 2>&1; then
  echo "Installing Python deps (set SADT_PIP_INSTALL=1 to force)…"
  pip install -q -r requirements.txt
fi
if [[ "${SADT_SD_DETECTOR_ENABLED}" == "1" ]] && ! python -c "import sd_detector" >/dev/null 2>&1; then
  echo "Installing sexual-deviance detector (editable)…"
  pip install -q -e "$ROOT/sexual-deviance"
fi

SD_HOST="${SADT_SD_LLAMA_HOST:-127.0.0.1}"
SD_PORT="${SADT_SD_LLAMA_PORT:-8080}"
SIDECAR_PID=""
SIDECAR_OWNED=0

sd_sidecar_healthy() {
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsS --max-time 2 "http://${SD_HOST}:${SD_PORT}/health" >/dev/null 2>&1
}

start_sd_sidecar() {
  local script="$ROOT/sexual-deviance/scripts/start_sidecar.sh"
  local log_dir="$ROOT/logs"
  local log_file="$log_dir/sd-sidecar.log"
  if sd_sidecar_healthy; then
    echo "  sd sidecar:       already up at ${SD_HOST}:${SD_PORT}"
    return 0
  fi
  if [[ ! -f "$script" ]]; then
    echo "WARNING: sidecar script tidak ada: $script"
    echo "         analisis tetap jalan; fallback NudeNet"
    return 0
  fi
  mkdir -p "$log_dir"
  echo "Starting sexual-deviance sidecar on ${SD_HOST}:${SD_PORT} (load model dulu, warmup GPU)…"
  bash "$script" >>"$log_file" 2>&1 &
  SIDECAR_PID=$!
  SIDECAR_OWNED=1
  local i
  for i in $(seq 1 90); do
    if sd_sidecar_healthy; then
      echo "  sd sidecar:       ready (${i}s)"
      return 0
    fi
    if ! kill -0 "$SIDECAR_PID" 2>/dev/null; then
      echo "WARNING: sidecar exit sebelum /health. Lihat $log_file"
      echo "         analisis tetap jalan; fallback NudeNet"
      SIDECAR_PID=""
      SIDECAR_OWNED=0
      return 0
    fi
    sleep 2
  done
  echo "WARNING: sidecar belum ready setelah ~180s di ${SD_HOST}:${SD_PORT}"
  echo "         analisis tetap jalan; fallback NudeNet. Log: $log_file"
}

echo "Starting API on $API_HOST:$API_PORT (auto-restart on crash)"
echo "  OCR (image→text): ${SADT_OCR_ENABLED:-0}  backend=${SADT_OCR_BACKEND:-default}  gpu=${SADT_OCR_GPU:-0}"
echo "  workers:          ${SADT_WORKER_CONCURRENCY:-4}"
echo "  media_text:       ${SADT_MEDIA_TEXT_ENABLED}"
echo "  nudity detector:  ${SADT_NUDITY_DETECTION_ENABLED}"
echo "  sd detector:      ${SADT_SD_DETECTOR_ENABLED}  sidecar=${SD_HOST}:${SD_PORT}"
echo "  GPU AI stack:     ${SADT_GPU_STACK_ENABLED}  (whisper=${SADT_GPU_WHISPER_ENABLED} icm=${SADT_GPU_ICM_ENABLED} qwen=${SADT_GPU_QWEN_ENABLED} clip=${SADT_CLIP_TOKOH_ENABLED})"

if [[ "${SADT_SD_DETECTOR_ENABLED}" == "1" ]]; then
  start_sd_sidecar
fi

# iPhone USB default di WSL. Windows AMDS hanya saat pasang WDA.
if grep -qi microsoft /proc/version 2>/dev/null; then
  IPHONE_WSL="$ROOT/ios-media-puller/ios_automator/scripts/ensure_iphone_wsl.sh"
  if [[ -f "$IPHONE_WSL" ]]; then
    echo "iPhone USB → WSL (default SATRIA)…"
    bash "$IPHONE_WSL" --startup || true
  fi
fi

# Watchdog: if uvicorn dies (OOM/Killed:9), bring it back without killing Vite.
# Do not restart after Ctrl+C / SIGTERM (uvicorn often exits 0 after graceful stop).
API_WATCHDOG_PID=""
(
  trap 'exit 0' INT TERM
  while true; do
    uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" --workers 1
    ec=$?
    if [[ "$ec" -eq 0 || "$ec" -eq 130 || "$ec" -eq 143 ]]; then
      exit "$ec"
    fi
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
  if [[ -n "${UI_PID:-}" ]]; then
    kill "$UI_PID" 2>/dev/null || true
  fi
  if [[ -n "${API_WATCHDOG_PID:-}" ]]; then
    pkill -P "$API_WATCHDOG_PID" 2>/dev/null || true
    kill "$API_WATCHDOG_PID" 2>/dev/null || true
    if command -v lsof >/dev/null 2>&1; then
      PIDS="$(lsof -ti:"$API_PORT" || true)"
      if [[ -n "${PIDS}" ]]; then
        # shellcheck disable=SC2086
        kill $PIDS 2>/dev/null || true
      fi
    fi
    wait "$API_WATCHDOG_PID" 2>/dev/null || true
  fi
  if [[ -n "${UI_PID:-}" ]]; then
    wait "$UI_PID" 2>/dev/null || true
  fi
  if [[ "${SIDECAR_OWNED}" == "1" && -n "${SIDECAR_PID}" ]]; then
    kill "$SIDECAR_PID" 2>/dev/null || true
    wait "$SIDECAR_PID" 2>/dev/null || true
  fi
  pkill -P $$ 2>/dev/null || true
  if command -v lsof >/dev/null 2>&1; then
    extra="$(lsof -ti:"${SD_PORT}" || true)"
    if [[ -n "${extra}" ]]; then
      # shellcheck disable=SC2086
      kill $extra 2>/dev/null || true
    fi
  fi
}

stop_from_signal() {
  trap - EXIT INT TERM
  cleanup
  exit 130
}
trap stop_from_signal INT TERM
trap cleanup EXIT

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
wait "$UI_PID" "$API_WATCHDOG_PID"
