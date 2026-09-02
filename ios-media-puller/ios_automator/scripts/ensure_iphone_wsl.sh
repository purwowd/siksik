#!/usr/bin/env bash
# Default: iPhone USB di WSL. --startup = UAC bind kalau masih di Windows.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ios_usb.sh
source "$HERE/ios_usb.sh"

if [[ "${1:-}" == "--startup" ]]; then
  ios_usb_claim_wsl
else
  ios_usb_ensure_wsl
fi
