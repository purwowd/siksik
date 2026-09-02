#!/usr/bin/env bash
# Dipanggil dari install_wda_windows.ps1. USB dipegang Windows AMDS;
# usbmux masuk ke WSL lewat TCP supaya AltServer-Linux nulis lewat driver native.
set -euo pipefail

export PATH="${HOME}/bin:${HOME}/.local/bin:${PATH}"
: "${USBMUXD_SOCKET_ADDRESS:?set USBMUXD_SOCKET_ADDRESS to Windows-host:27015}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

echo "[windows-mux] USBMUXD_SOCKET_ADDRESS=${USBMUXD_SOCKET_ADDRESS}"

if lsusb -d 05ac:12a8 >/dev/null 2>&1; then
  echo "[windows-mux] iPhone masih tampil di lsusb WSL — USB masih di usbipd, bukan Windows." >&2
  echo "[windows-mux] Lepas dulu: usbipd detach, pastikan Apple Mobile Device Service Running." >&2
  exit 2
fi

echo "[windows-mux] tunggu iPhone lewat usbmux Windows…"

UDID=""
for _ in $(seq 1 20); do
  UDID="$(idevice_id -l 2>/dev/null | head -1 || true)"
  if [[ -n "$UDID" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$UDID" ]]; then
  echo "[windows-mux] iPhone tidak terlihat. Unlock, Trust This Computer, pastikan Apple Mobile Device Service Running." >&2
  exit 2
fi

echo "[windows-mux] UDID=${UDID}"
# shellcheck source=ios_usb.sh
source "$ROOT/ios_automator/scripts/ios_usb.sh"
if ! ios_usb_lockdown_ok "$UDID"; then
  echo "[windows-mux] lockdownd belum siap — pair / Trust"
fi
if ! idevicepair validate; then
  echo "[windows-mux] pairing belum valid — tap Trust di iPhone jika muncul"
  idevicepair pair || true
  idevicepair validate
fi

set +e
ios_usb_wda_status "$UDID"
wda_st=$?
set -e
if [[ "$wda_st" -eq 0 ]]; then
  echo "[windows-mux] WDA sudah terpasang — skip AltServer."
  exit 0
fi

exec bash "$ROOT/ios_automator/scripts/install_wda_altserver.sh"
