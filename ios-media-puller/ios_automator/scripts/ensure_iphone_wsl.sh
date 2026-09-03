#!/usr/bin/env bash
# Default: attach iPhone yang sudah Shared / Shared (forced) ke WSL (tanpa UAC)
# bila lsusb sudah melihat device.
# --startup = claim WSL; UAC bind --force jika Not shared ATAU Shared tapi
# Device busy (AMDS Windows masih pegang). Jangan dipanggil dari start_poc.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ios_usb.sh
source "$HERE/ios_usb.sh"

if [[ "${1:-}" == "--startup" ]]; then
  ios_usb_claim_wsl
else
  ios_usb_ensure_wsl
fi
