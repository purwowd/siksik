#!/usr/bin/env bash
# Lepas iPhone dari WSL usbipd supaya Windows AMDS bisa pasang WDA. Tanpa UAC.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ios_usb.sh
source "$HERE/ios_usb.sh"

ios_usb_release_to_windows
