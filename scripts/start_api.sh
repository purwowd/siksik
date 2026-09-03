#!/usr/bin/env bash
# SATRIA API only (FastAPI + optional SD sidecar). No Vite — for systemd / Tauri desktop.
# Usage: bash scripts/start_api.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

if [[ -d /usr/lib/wsl/lib ]]; then
  export PATH="/usr/lib/wsl/lib:${PATH:-}"
  export LD_LIBRARY_PATH="/usr/lib/wsl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

if [[ -z "${ANDROID_HOME:-}" && -n "${ANDROID_SDK_ROOT:-}" ]]; then
  export ANDROID_HOME="$ANDROID_SDK_ROOT"
fi
if [[ -z "${ANDROID_HOME:-}" ]]; then
  for sdk_candidate in "${HOME}/Android/Sdk" "${HOME}/Android/sdk" "/usr/lib/android-sdk"; do
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
if [[ "$(uname -s)" == "Darwin" ]]; then
  API_HOST="${SADT_API_HOST:-127.0.0.1}"
else
  API_HOST="${SADT_API_HOST:-0.0.0.0}"
fi
export SADT_API_HOST="$API_HOST"
export SADT_API_PORT="$API_PORT"
export SATRIA_API_PORT="${SATRIA_API_PORT:-$API_PORT}"

: "${SADT_OCR_ENABLED:=1}"
: "${SADT_OCR_BACKEND:=easyocr}"
: "${SADT_OCR_GPU:=0}"
: "${SADT_GPU_STACK_ENABLED:=0}"
: "${SADT_GPU_WHISPER_ENABLED:=0}"
: "${SADT_GPU_SAFEWATCH_ENABLED:=0}"
: "${SADT_GPU_ICM_ENABLED:=0}"
: "${SADT_GPU_QWEN_ENABLED:=0}"
: "${SADT_CLIP_TOKOH_ENABLED:=0}"
: "${SADT_MEDIA_TEXT_ENABLED:=0}"
: "${SADT_NUDITY_DETECTION_ENABLED:=1}"
: "${SADT_SD_DETECTOR_ENABLED:=0}"
export SADT_OCR_ENABLED SADT_OCR_BACKEND SADT_OCR_GPU \
  SADT_GPU_STACK_ENABLED SADT_GPU_WHISPER_ENABLED \
  SADT_GPU_SAFEWATCH_ENABLED SADT_GPU_ICM_ENABLED SADT_GPU_QWEN_ENABLED \
  SADT_CLIP_TOKOH_ENABLED SADT_MEDIA_TEXT_ENABLED SADT_NUDITY_DETECTION_ENABLED \
  SADT_SD_DETECTOR_ENABLED

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

free_port() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
  elif command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -ti:"$port" || true)"
    if [[ -n "${pids}" ]]; then
      # shellcheck disable=SC2086
      kill -9 $pids || true
    fi
  fi
}

# Only clear the API port when SATRIA_API_FORCE_BIND=1 (systemd install / restart).
if [[ "${SATRIA_API_FORCE_BIND:-0}" == "1" ]]; then
  free_port "$API_PORT"
  sleep 1
fi

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  echo "ERROR: missing $ROOT/backend/.venv — create with python3 -m venv .venv && pip install -r requirements.txt" >&2
  exit 2
fi
# shellcheck disable=SC1091
source .venv/bin/activate

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

if ! python -c "import uvicorn, fastapi" >/dev/null 2>&1; then
  echo "ERROR: uvicorn/fastapi missing in .venv" >&2
  exit 2
fi

SD_HOST="${SADT_SD_LLAMA_HOST:-127.0.0.1}"
SD_PORT="${SADT_SD_LLAMA_PORT:-8080}"
SIDECAR_PID=""
SIDECAR_OWNED=0
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

sd_sidecar_healthy() {
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsS --max-time 2 "http://${SD_HOST}:${SD_PORT}/health" >/dev/null 2>&1
}

start_sd_sidecar() {
  local script="$ROOT/sexual-deviance/scripts/start_sidecar.sh"
  local log_file="$LOG_DIR/sd-sidecar.log"
  if sd_sidecar_healthy; then
    echo "sd sidecar: already up at ${SD_HOST}:${SD_PORT}"
    return 0
  fi
  if [[ ! -f "$script" ]]; then
    echo "WARNING: sidecar script missing: $script"
    return 0
  fi
  echo "Starting sexual-deviance sidecar on ${SD_HOST}:${SD_PORT}…"
  bash "$script" >>"$log_file" 2>&1 &
  SIDECAR_PID=$!
  SIDECAR_OWNED=1
  local i
  for i in $(seq 1 90); do
    if sd_sidecar_healthy; then
      echo "sd sidecar: ready (${i}s)"
      return 0
    fi
    if ! kill -0 "$SIDECAR_PID" 2>/dev/null; then
      echo "WARNING: sidecar exited before /health — see $log_file"
      SIDECAR_PID=""
      SIDECAR_OWNED=0
      return 0
    fi
    sleep 2
  done
  echo "WARNING: sidecar not ready after ~180s — see $log_file"
}

cleanup() {
  if [[ "${SIDECAR_OWNED}" == "1" && -n "${SIDECAR_PID}" ]]; then
    kill "$SIDECAR_PID" 2>/dev/null || true
    wait "$SIDECAR_PID" 2>/dev/null || true
    SIDECAR_OWNED=0
    SIDECAR_PID=""
  fi
}
on_signal() {
  cleanup
  trap - EXIT INT TERM
  exit 143
}
trap cleanup EXIT
trap on_signal INT TERM

if [[ "${SADT_SD_DETECTOR_ENABLED}" == "1" ]]; then
  start_sd_sidecar
fi

if grep -qi microsoft /proc/version 2>/dev/null; then
  python -c "import asyncio; from app.acquisition.android_usb_wsl import ensure_shared_wsl_usb; asyncio.run(ensure_shared_wsl_usb())" 2>/dev/null || true
fi

echo "SATRIA API on ${API_HOST}:${API_PORT} (systemd / no Vite)"
# No exec: keep this shell so EXIT/INT/TERM can stop an owned sidecar.
set +e
uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" --workers 1
ec=$?
set -e
cleanup
trap - EXIT INT TERM
exit "$ec"
