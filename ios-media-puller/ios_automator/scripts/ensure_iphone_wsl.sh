#!/usr/bin/env bash
# Default: attach iPhone yang sudah Shared ke WSL (tanpa UAC).
# --startup = UAC bind --force: Pindai ulang / Jalankan akuisisi saat Not shared
# atau Deny AutoBind (sisa pasang WDA). Jangan dipanggil dari start_poc.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ios_usb.sh
source "$HERE/ios_usb.sh"

if [[ "${1:-}" == "--startup" ]]; then
  ios_usb_claim_wsl
else
  ios_usb_ensure_wsl
fi
