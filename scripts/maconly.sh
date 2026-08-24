#!/usr/bin/env bash
# Lab Mac SATRIA: API :8000 + UI Vite :5173 (bukan Tauri).
# Env lab (OCR EasyOCR CPU, GPU stack off) di repo-root .env
# Jalankan: bash scripts/maconly.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/start_poc.sh"
