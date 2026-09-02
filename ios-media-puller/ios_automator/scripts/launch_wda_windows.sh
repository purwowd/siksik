#!/usr/bin/env bash
# Dari WSL: launch WebDriverAgent lewat go-ios Windows (AMDS), bukan usbipd.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WIN_DIR="/mnt/c/Users/Admin/wda"
UDID="${1:-${UDID:-}}"
BUNDLE="${2:-com.facebook.WebDriverAgentRunner.xctrunner}"
mkdir -p "$WIN_DIR"
cp -f "$HERE/launch_wda_windows.ps1" "$WIN_DIR/launch_wda_windows.ps1"
PS1="C:\\Users\\Admin\\wda\\launch_wda_windows.ps1"

args=("-NoProfile" "-ExecutionPolicy" "Bypass" "-File" "$PS1")
if [[ -n "$UDID" ]]; then
  args+=("-Udid" "$UDID")
fi
args+=("-Bundle" "$BUNDLE")

powershell.exe "${args[@]}"
exit $?
