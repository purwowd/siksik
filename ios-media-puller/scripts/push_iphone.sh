#!/usr/bin/env bash
# Push IPA ke iPhone lewat USB native Windows (AMDS + go-ios).
# Signing tidak diubah. Tidak memakai usbipd sebagai transport.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PS1_SRC="$HERE/push_iphone_windows.ps1"
WIN_DIR="/mnt/c/Users/Admin/wda"
WIN_IOS="$WIN_DIR/ios.exe"

usage() {
  echo "Usage: push_iphone.sh <path-to-ipa>" >&2
  exit 2
}

IPA="${1:-}"
[[ -n "$IPA" ]] || usage
if [[ ! -f "$IPA" ]]; then
  echo "[ERROR] IPA file not found: $IPA" >&2
  exit 2
fi

IPA="$(readlink -f "$IPA")"
echo "[INFO] IPA found: $IPA"

WIN_IPA="$(wslpath -w "$IPA")"
echo "[INFO] Windows IPA path: $WIN_IPA"

if [[ ! -f "$WIN_IOS" ]]; then
  echo "[ERROR] go-ios not found: C:\\Users\\Admin\\wda\\ios.exe" >&2
  exit 2
fi
if [[ ! -f "$PS1_SRC" ]]; then
  echo "[ERROR] missing helper: $PS1_SRC" >&2
  exit 2
fi

mkdir -p "$WIN_DIR"
cp -f "$PS1_SRC" "$WIN_DIR/push_iphone_windows.ps1"
WIN_PS1="C:\\Users\\Admin\\wda\\push_iphone_windows.ps1"

set +e
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$WIN_PS1" "$WIN_IPA"
rc=$?
set -e
exit "$rc"
