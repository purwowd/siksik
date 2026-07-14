#!/usr/bin/env bash
# Deploy + smoke di server GPU
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt

# Opsional CUDA torch (uncomment sesuai CUDA server):
# pip install torch --index-url https://download.pytorch.org/whl/cu124

echo "== Acceptance =="
python scripts/run_acceptance.py --perf --require-gpu

echo "== Start API =="
# Default loopback; override SADT_API_HOST=0.0.0.0 only on trusted LAN + TLS terminator
API_HOST="${SADT_API_HOST:-127.0.0.1}"
exec uvicorn app.main:app --host "$API_HOST" --port "${SADT_API_PORT:-8000}" --workers 1
