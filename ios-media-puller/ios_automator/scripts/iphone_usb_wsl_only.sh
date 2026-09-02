#!/usr/bin/env bash
# Dari WSL: UAC Windows, lalu iPhone USB di-bind ke WSL saja (bukan AMDS).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/iphone_usb_wsl_only.ps1"
WIN_DIR="/mnt/c/Users/Admin/wda"
WIN_COPY="$WIN_DIR/iphone_usb_wsl_only.ps1"

mkdir -p "$WIN_DIR"
cp -f "$SRC" "$WIN_COPY"
PS1="C:\\Users\\Admin\\wda\\iphone_usb_wsl_only.ps1"

echo "[wsl-usb] UAC Windows akan muncul — pilih Yes."
echo "[wsl-usb] AltServer Windows di-kill, iPhone attach ke WSL. AMDS dibiarkan Running."

# Jangan -Wait: usbipd --auto-attach jadi child dan Start-Process -Wait tidak pernah kembali.
powershell.exe -NoProfile -Command \
  "Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','${PS1}'"

echo "[wsl-usb] menunggu usbipd Attached…"
for _ in $(seq 1 45); do
  policy="$(usbipd.exe policy list 2>/dev/null | tr -d '\r')"
  line="$(usbipd.exe list 2>/dev/null | tr -d '\r' | awk '/05ac:12a8/ {print; exit}')"
  state="$(printf '%s\n' "$line" | awk '{print $NF}')"
  busid="$(printf '%s\n' "$line" | awk '{print $1}')"
  allow="$(printf '%s\n' "$policy" | awk '/Allow[[:space:]]+AutoBind/ && /05ac:12a8/ {print "yes"; exit}')"
  deny="$(printf '%s\n' "$policy" | awk '/Deny[[:space:]]+AutoBind/ && /05ac:12a8/ {print "yes"; exit}')"
  if [[ "$allow" == "yes" && "$deny" != "yes" ]]; then
    if [[ "$state" == "Attached" ]] || lsusb -d 05ac:12a8 >/dev/null 2>&1; then
      echo "[wsl-usb] usbipd=${state:-unplugged}  AutoBind=Allow"
      exit 0
    fi
    if [[ -n "$busid" && "$state" != "Attached" ]]; then
      usbipd.exe attach --wsl --busid "$busid" >/dev/null 2>&1 || true
    fi
  fi
  if [[ "$state" == "Attached" ]] || lsusb -d 05ac:12a8 >/dev/null 2>&1; then
    echo "[wsl-usb] usbipd=${state:-lsusb} (tanpa AutoBind)"
    exit 0
  fi
  sleep 2
done
echo "[wsl-usb] timeout menunggu UAC/skrip. Cek: usbipd.exe list" >&2
exit 1
