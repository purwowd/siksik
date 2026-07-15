#!/usr/bin/env bash
# Deploy + smoke di server GPU (Linux)
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt -r requirements-gpu.txt

# Torch CUDA — uncomment sesuai CUDA server:
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

echo "== Acceptance =="
SADT_REQUIRE_GPU=1 python scripts/run_acceptance.py --perf --require-gpu

echo "== Start API (GPU stack via run.py) =="
API_HOST="${SADT_API_HOST:-127.0.0.1}"
exec python run.py --host "$API_HOST" --port "${SADT_API_PORT:-8000}" --gpu
