#!/usr/bin/env bash
# Dari WSL: nyalakan AMDS kalau mati. Skip UAC jika sudah Running.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WIN_DIR="/mnt/c/Users/Admin/wda"
mkdir -p "$WIN_DIR"
cp -f "$HERE/ensure_amds_windows.ps1" "$WIN_DIR/ensure_amds_windows.ps1"

status="$(powershell.exe -NoProfile -Command "(Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue).Status" 2>/dev/null | tr -d '\r' | awk 'NF{print $1; exit}')"
if [[ "$status" == "Running" ]]; then
  echo "[amds] Apple Mobile Device Service sudah Running"
  exit 0
fi

echo "[amds] AMDS=${status:-unknown} — UAC Windows: pilih Yes supaya Linux bisa push WDA."
cd "$WIN_DIR"
set +e
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\\Users\\Admin\\wda\\ensure_amds_windows.ps1"
ec=$?
set -e
if [[ "$ec" -ne 0 ]]; then
  echo "[amds] gagal (exit $ec). Dari PowerShell Windows:" >&2
  echo "  Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\\Users\\Admin\\wda\\ensure_amds_windows.ps1'" >&2
  exit "$ec"
fi
exit 0
