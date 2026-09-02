#!/usr/bin/env bash
# Unwedge iPhone lockdownd after a failed IPA write / hung ideviceinstaller.
# Does not install WDA.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ios_usb.sh
source "$HERE/ios_usb.sh"

UDID="${UDID:-$(idevice_id -l 2>/dev/null | head -1 || true)}"
if [[ -z "$UDID" ]]; then
  echo "[usb] idevice_id kosong. Unlock iPhone, colok USB, tap Trust." >&2
  exit 2
fi

if ios_usb_lockdown_ok "$UDID"; then
  echo "[usb] lockdownd sudah hidup (UDID=$UDID)"
  ios_usb_timeout 8 ideviceinfo -u "$UDID" -k DeviceName -k ProductVersion || true
  exit 0
fi

ios_usb_recover "$UDID"
rc=$?
if [[ "$rc" -eq 0 ]]; then
  echo "[usb] cek daftar app:"
  set +e
  ios_usb_wda_status "$UDID"
  st=$?
  set -e
  case "$st" in
    0) echo "[usb] WDA sudah terlihat di ideviceinstaller -l" ;;
    1) echo "[usb] USB sehat; WDA belum terpasang. Install lewat Windows mux, bukan WSL usbipd." ;;
    2) echo "[usb] ideviceinstaller tidak ada" ;;
    *) echo "[usb] list app masih gagal (status $st)" ;;
  esac
  exit 0
fi
exit "$rc"
