#!/usr/bin/env bash
# Start llama-server sidecar — CUDA (Linux/WSL), Metal (macOS), atau CPU
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOCAL_CUDA="$(cd "$(dirname "$0")/.." && pwd)/.cuda-12.8/usr/local/cuda-12.8"
if [[ -z "${CUDA_HOME:-}" ]]; then
  if [[ -x "$LOCAL_CUDA/bin/nvcc" ]]; then
    export CUDA_HOME="$LOCAL_CUDA"
  else
    export CUDA_HOME=/usr/local/cuda
  fi
fi
export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64:/usr/local/cuda/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"

CONFIG="${CONFIG:-config.yaml}"
PYTHON="${ROOT}/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

eval "$("$PYTHON" - <<PY
import yaml
c = yaml.safe_load(open("$CONFIG"))
ll = c["llama"]
print(f'SERVER_BIN={ll["server_bin"]!r}')
print(f'HOST={ll["host"]!r}')
print(f'PORT={ll["port"]!r}')
print(f'NGL={ll["n_gpu_layers"]!r}')
print(f'CTX={ll["ctx_size"]!r}')
print(f'THREADS={ll.get("threads", 4)!r}')
print(f'MODEL_HF={ll.get("model_hf", "ggml-org/SmolVLM-500M-Instruct-GGUF")!r}')
PY
)"

if [[ ! -x "$SERVER_BIN" && ! -f "$SERVER_BIN" ]]; then
  echo "llama-server tidak ditemukan: $SERVER_BIN"
  echo "Jalankan: ./scripts/setup.sh"
  exit 1
fi

MODEL_DIR="${ROOT}/models"
MODEL=$(find "$MODEL_DIR" -name "*.gguf" ! -name "*mmproj*" 2>/dev/null | head -1)
MMPROJ=$(find "$MODEL_DIR" -name "*mmproj*.gguf" 2>/dev/null | head -1)

# Mac Metal: unified RAM, default llama-server (-np auto ≈ 4, ctx 4096) is fine.
# WSL/Linux RTX 4050 6GB: 4 slots × 4096 KV wastes VRAM and adds CUDA launch cost
# for sequential CLI. One slot + flash-attn is the usual win on Ada laptops.
EXTRA=()
if [[ "$(uname -s)" == "Linux" ]]; then
  if [[ "${CTX}" -gt 2048 ]]; then
    CTX=2048
  fi
  EXTRA+=(-np 1 -fa on -ub 256 -t "${THREADS:-4}")
fi

echo "Starting llama-server sidecar on ${HOST}:${PORT}  ngl=${NGL} ctx=${CTX} extra=${EXTRA[*]:-none}"

if [[ -n "$MODEL" ]]; then
  echo "  model (local): $MODEL"
  exec "$SERVER_BIN" \
    --host "$HOST" \
    --port "$PORT" \
    -m "$MODEL" \
    ${MMPROJ:+--mmproj "$MMPROJ"} \
    -ngl "$NGL" \
    -c "$CTX" \
    "${EXTRA[@]}"
else
  echo "  model (HF): $MODEL_HF"
  echo "  (untuk offline: salin .gguf ke ./models/)"
  exec "$SERVER_BIN" \
    --host "$HOST" \
    --port "$PORT" \
    -hf "$MODEL_HF" \
    -ngl "$NGL" \
    -c "$CTX" \
    "${EXTRA[@]}"
fi
