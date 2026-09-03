#!/usr/bin/env bash
# Dari WSL: prefer attach diam (Shared / Shared forced). UAC bila Not shared
# atau Shared tapi Device busy (AMDS masih pegang — butuh bind --force).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ios_usb.sh
source "$HERE/ios_usb.sh"

if ios_usb_iphone_ready; then
  echo "[wsl-usb] iPhone sudah di WSL (lsusb)"
  exit 0
fi
if ios_usb_attach_shared; then
  echo "[wsl-usb] attach diam (tanpa UAC)"
  exit 0
fi
if ios_usb_iphone_ready; then
  echo "[wsl-usb] iPhone siap setelah attach"
  exit 0
fi

line="$(ios_usb_apple_line)"
state="$(ios_usb_apple_state "$line")"
if [[ -z "$line" ]]; then
  echo "[wsl-usb] tidak ada iPhone di usbipd" >&2
  exit 1
fi
if [[ "$state" != "not_shared" && "$state" != "shared" ]]; then
  echo "[wsl-usb] iPhone state=${state:-unknown} — skip UAC; cek: usbipd.exe list" >&2
  exit 1
fi

SRC="$HERE/iphone_usb_wsl_only.ps1"
WIN_DIR="/mnt/c/Users/Admin/wda"
WIN_COPY="$WIN_DIR/iphone_usb_wsl_only.ps1"

mkdir -p "$WIN_DIR"
cp -f "$SRC" "$WIN_COPY"
PS1="C:\\Users\\Admin\\wda\\iphone_usb_wsl_only.ps1"

echo "[wsl-usb] iPhone ${state} tanpa lsusb — UAC Windows (bind --force) supaya WSL pegang USB."
echo "[wsl-usb] AltServer Windows di-kill, iPhone attach ke WSL."

# Jangan -Wait: usbipd --auto-attach jadi child dan Start-Process -Wait tidak pernah kembali.
powershell.exe -NoProfile -Command \
  "Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','${PS1}'"

echo "[wsl-usb] menunggu usbipd Attached…"
for _ in $(seq 1 45); do
  if ios_usb_iphone_in_lsusb; then
    echo "[wsl-usb] iPhone di WSL (lsusb)"
    exit 0
  fi
  line="$(ios_usb_apple_line)"
  state="$(ios_usb_apple_state "$line")"
  busid="$(printf '%s\n' "$line" | awk '{print $1}')"
  if [[ "$state" == "attached" ]]; then
    echo "[wsl-usb] usbipd=Attached"
    exit 0
  fi
  if [[ "$state" == "shared" && -n "$busid" ]]; then
    usbipd.exe attach --wsl --busid "$busid" >/dev/null 2>&1 || true
  fi
  sleep 2
done
echo "[wsl-usb] timeout menunggu UAC/skrip. Cek: usbipd.exe list" >&2
exit 1
