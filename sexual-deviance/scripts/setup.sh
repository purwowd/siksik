#!/usr/bin/env bash
# Setup: llama.cpp CUDA (RTX) + SmolVLM-500M + NudeNet 320n
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL_HF="${MODEL_HF:-ggml-org/SmolVLM-500M-Instruct-GGUF}"
MODEL_FILE="${MODEL_FILE:-SmolVLM-500M-Instruct-Q8_0.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-mmproj-SmolVLM-500M-Instruct-f16.gguf}"
NPROC="$(nproc 2>/dev/null || echo 4)"

LOCAL_CUDA="$ROOT/.cuda-12.8/usr/local/cuda-12.8"
if [[ -x "$LOCAL_CUDA/bin/nvcc" ]]; then
  export CUDA_HOME="$LOCAL_CUDA"
elif [[ -z "${CUDA_HOME:-}" ]]; then
  export CUDA_HOME=/usr/local/cuda
fi
export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64:/usr/local/cuda/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="/usr/lib/wsl/lib:${LIBRARY_PATH:-}"
export CUDAToolkit_ROOT="${CUDA_HOME}"

have_cuda() {
  command -v nvcc >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1
}

cuda_arch() {
  local cap
  cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' .')"
  if [[ -n "$cap" ]]; then
    echo "$cap"
  else
    echo "89"
  fi
}

nvcc_ok_for_host() {
  # CUDA 12.0 nvcc + Ubuntu 24.04 glibc (_Float32) fails compiler-id.
  local ver
  ver="$("$CUDA_HOME/bin/nvcc" --version 2>/dev/null | sed -n 's/.*release \([0-9]\+\)\.\([0-9]\+\).*/\1\2/p' | head -1)"
  [[ -n "$ver" && "$ver" -ge 124 ]]
}

if [ ! -d llama.cpp ]; then
  echo "==> Clone llama.cpp"
  git clone --depth 1 https://github.com/ggml-org/llama.cpp.git
fi

echo "==> Build llama-server"
cd llama.cpp
if [[ "$(uname -s)" == "Darwin" ]]; then
  cmake -B build -DGGML_METAL=ON 2>/dev/null || cmake -B build
elif have_cuda && nvcc_ok_for_host; then
  ARCH="${CMAKE_CUDA_ARCHITECTURES:-$(cuda_arch)}"
  echo "    CUDA ${CUDA_HOME} — GGML_CUDA=ON arch=${ARCH}"
  cmake -B build \
    -DGGML_CUDA=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CUDA_ARCHITECTURES="${ARCH}" \
    -DCMAKE_CUDA_COMPILER="${CUDA_HOME}/bin/nvcc" \
    -DCMAKE_CUDA_HOST_COMPILER="$(command -v g++)"
else
  echo "    No usable CUDA toolkit — CPU build"
  echo "    (WSL: system CUDA 12.0 is too old; extract 12.8+ to .cuda-12.8/)"
  cmake -B build -DCMAKE_BUILD_TYPE=Release
fi
cmake --build build --config Release -j "$NPROC" --target llama-server
cd "$ROOT"

if [[ -x ./llama.cpp/build/bin/llama-server ]]; then
  echo "==> llama-server devices"
  ./llama.cpp/build/bin/llama-server --list-devices 2>/dev/null || true
fi

echo "==> Python deps"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip -q
pip install -r requirements.txt -q
pip install -e . -q
pip install huggingface_hub -q

echo "==> Verify NudeNet 320n (bundled)"
mkdir -p models
if [ -f models/640m.onnx ]; then
  SIZE=$(wc -c < models/640m.onnx | tr -d ' ')
  if [ "$SIZE" -lt 1000000 ]; then
    echo "    Menghapus models/640m.onnx corrupt (${SIZE} bytes)"
    rm -f models/640m.onnx
  fi
fi
python -c "
from nudenet import NudeDetector
NudeDetector(inference_resolution=320)
print('NudeNet 320n OK (bundled)')
"

echo "==> Download VLM GGUF: ${MODEL_HF}"
python - <<PY
from huggingface_hub import hf_hub_download

repo = "${MODEL_HF}"
files = ["${MODEL_FILE}", "${MMPROJ_FILE}"]
for name in files:
    path = hf_hub_download(repo_id=repo, filename=name, local_dir="models")
    print(f"    {name} -> {path}")
PY

echo "==> Download test samples"
python scripts/download_samples.py || echo "WARNING: beberapa sample gagal (rate limit Wikimedia OK)"

echo ""
echo "Setup selesai!"
echo "  source .venv/bin/activate"
echo "  ./scripts/start_sidecar.sh          # terminal 1 (CUDA -ngl dari config.yaml)"
echo "  python main.py --external-server samples/internet/*.jpg"
