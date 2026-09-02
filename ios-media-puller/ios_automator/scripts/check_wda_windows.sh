#!/usr/bin/env bash
# Dari WSL: list app iPhone lewat go-ios Windows (AMDS), bukan usbipd/lockdownd.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WIN_DIR="/mnt/c/Users/Admin/wda"
UDID="${1:-${UDID:-}}"
mkdir -p "$WIN_DIR"
cp -f "$HERE/list_wda_windows.ps1" "$WIN_DIR/list_wda_windows.ps1"
PS1="C:\\Users\\Admin\\wda\\list_wda_windows.ps1"

args=("-NoProfile" "-ExecutionPolicy" "Bypass" "-File" "$PS1")
if [[ -n "$UDID" ]]; then
  args+=("-Udid" "$UDID")
fi

powershell.exe "${args[@]}"
exit $?
