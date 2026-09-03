# USB / lockdown helpers for WDA install and listing.
# Source from other scripts. Do not execute as a program.

_IOS_USB_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_IOS_USB_ROOT="$(cd "$_IOS_USB_SCRIPTS/../.." && pwd)"

ios_usb_is_wsl() {
  grep -qi microsoft /proc/version 2>/dev/null
}

ios_usb_windows_mux() {
  [[ -n "${USBMUXD_SOCKET_ADDRESS:-}" ]]
}

ios_usb_tcp_open() {
  local spec="${1:-}"
  local host="${spec%:*}"
  local port="${spec##*:}"
  [[ -n "$host" && -n "$port" && "$host" != "$spec" ]] || return 1
  python3 - "$host" "$port" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(1.2)
try:
    s.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

ios_usb_windows_mux_addr() {
  local gw
  if [[ -n "${USBMUXD_SOCKET_ADDRESS:-}" ]]; then
    if ios_usb_tcp_open "$USBMUXD_SOCKET_ADDRESS"; then
      printf '%s\n' "$USBMUXD_SOCKET_ADDRESS"
      return 0
    fi
  fi
  gw="$(ip route show default 2>/dev/null | awk '/default/{print $3; exit}')"
  if [[ -n "$gw" ]] && ios_usb_tcp_open "${gw}:27015"; then
    printf '%s\n' "${gw}:27015"
    return 0
  fi
  return 1
}

ios_usb_amds_status() {
  powershell.exe -NoProfile -Command "(Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue).Status" 2>/dev/null | tr -d '\r' | awk 'NF{print $1; exit}'
}

ios_usb_ensure_amds() {
  local sh="$_IOS_USB_ROOT/ios_automator/scripts/ensure_amds_windows.sh"
  if [[ ! -f "$sh" ]]; then
    return 1
  fi
  bash "$sh"
}

ios_usb_mux_has_device() {
  local mux="${1:-}"
  local udid
  [[ -n "$mux" ]] || return 1
  udid="$(USBMUXD_SOCKET_ADDRESS="$mux" ios_usb_timeout 8 idevice_id -l 2>/dev/null | awk 'NF{print $1; exit}')"
  [[ -n "$udid" ]]
}

ios_usb_linux_mux_install() {
  local mux ec had_e=0
  mux="$(ios_usb_windows_mux_addr)" || true
  if [[ -z "$mux" ]]; then
    echo "[install] usbmux Windows :27015 tidak terbuka." >&2
    return 1
  fi
  if ! ios_usb_mux_has_device "$mux"; then
    echo "[install] AMDS belum melihat iPhone di $mux." >&2
    return 1
  fi
  echo "[install] push WDA dari Linux lewat usbmux Windows ($mux)." >&2
  echo "[install] Kode 6 digit Apple: ketik di terminal ini, lalu Enter." >&2
  case $- in *e*) had_e=1 ;; esac
  set +e
  USBMUXD_SOCKET_ADDRESS="$mux" bash "$_IOS_USB_ROOT/ios_automator/scripts/install_wda_via_windows_mux.sh"
  ec=$?
  if [[ "$had_e" -eq 1 ]]; then
    set -e
  fi
  return "$ec"
}

ios_usb_iphone_in_lsusb() {
  lsusb -d 05ac:12a8 >/dev/null 2>&1 || lsusb -d 05ac:12ab >/dev/null 2>&1
}

# WSL usbipd owns the cable. Distro usbmuxd 1.1.1 drops 65536-byte packets
# during IPA AFC write and wedges lockdownd.
ios_usb_wsl_direct() {
  ios_usb_is_wsl && ios_usb_iphone_in_lsusb && ! ios_usb_windows_mux
}

ios_usb_timeout() {
  local sec="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$sec" "$@"
  else
    "$@"
  fi
}

ios_usb_lockdown_ok() {
  local udid="${1:-}"
  if [[ -n "$udid" ]]; then
    ios_usb_timeout 6 ideviceinfo -u "$udid" -k DeviceName >/dev/null 2>&1
  else
    ios_usb_timeout 6 ideviceinfo -k DeviceName >/dev/null 2>&1
  fi
}

ios_usb_blob_is_lockdown() {
  printf '%s' "${1:-}" | grep -qiE 'could not connect to lockdownd|mux error \(-8\)|lockdownd, error code'
}

ios_usb_mux_overflow() {
  journalctl -u usbmuxd -n 120 --no-pager 2>/dev/null |
    grep -q 'message was too large (65536 bytes, max = 65535)'
}

ios_usb_kill_stale() {
  pkill -x ideviceinstaller 2>/dev/null || true
  pkill -f '/AltServer( |$)' 2>/dev/null || true
  pkill -f 'ios tunnel start' 2>/dev/null || true
  pkill -f 'ios apps --list' 2>/dev/null || true
  sleep 0.4
}

# 0 = WDA listed, 1 = list ok but WDA absent, 2 = no ideviceinstaller, 3 = lockdown
ios_usb_wda_status() {
  local udid="${1:-${UDID:-}}"
  local out err rc blob had_e=0
  if ! command -v ideviceinstaller >/dev/null 2>&1; then
    return 2
  fi
  case $- in *e*) had_e=1 ;; esac
  err="$(mktemp /tmp/ios-usb-list.XXXXXX)"
  set +e
  if [[ -n "$udid" ]]; then
    out="$(ios_usb_timeout 12 ideviceinstaller -u "$udid" -l 2>"$err")"
  else
    out="$(ios_usb_timeout 12 ideviceinstaller -l 2>"$err")"
  fi
  rc=$?
  blob="${out}"$'\n'"$(cat "$err" 2>/dev/null || true)"
  rm -f "$err"
  if [[ "$had_e" -eq 1 ]]; then
    set -e
  fi
  if ios_usb_blob_is_lockdown "$blob" || [[ "$rc" -ne 0 ]]; then
    return 3
  fi
  if printf '%s\n' "$out" | grep -qiE 'webdriver|xctrunner'; then
    return 0
  fi
  return 1
}

ios_usb_prefer_windows_install() {
  ios_usb_is_wsl && ! ios_usb_windows_mux
}

ios_usb_print_wsl_ipa_block() {
  echo "[install] WSL usbipd + usbmuxd 1.1.1 tidak bisa menulis IPA (paket 65536)." >&2
  echo "[install] Signing Apple ID bisa sukses, lalu lockdownd mati di 'Writing to device'." >&2
  echo "[install] Pasang WDA lewat USB Windows:" >&2
  echo "  bash $_IOS_USB_ROOT/ios_automator/scripts/install_wda_windows.sh" >&2
  echo "[install] Pulihkan lockdownd (tanpa install):" >&2
  echo "  bash $_IOS_USB_ROOT/ios_automator/scripts/recover_ios_lockdown.sh" >&2
}

ios_usb_wda_install() {
  local ipa="${1:-}"
  local win="$_IOS_USB_ROOT/ios_automator/scripts/install_wda_windows.sh"
  local alt="$_IOS_USB_ROOT/ios_automator/scripts/install_wda_altserver.sh"
  if ios_usb_prefer_windows_install; then
    echo "[install] WDA belum ada — WSL memakai USB Windows (install_wda_windows.sh)." >&2
    echo "[install] UAC: pilih Yes. Kode 6 digit Apple diketik di jendela PowerShell." >&2
    bash "$win"
    return $?
  fi
  echo "[install] WDA belum ada — AltServer USB Linux." >&2
  if [[ -n "$ipa" ]]; then
    bash "$alt" "$ipa"
  else
    bash "$alt"
  fi
}

# Apple composite VID only. Android buses must never be detached to Windows.
ios_usb_recycle_usbipd() {
  local busid line
  if ! command -v usbipd.exe >/dev/null 2>&1; then
    return 1
  fi
  line="$(ios_usb_apple_line)"
  busid="$(printf '%s\n' "$line" | awk '{print $1}')"
  if [[ -z "$busid" || "$line" != *05ac:* ]]; then
    return 1
  fi
  echo "[usb] usbipd detach $busid" >&2
  usbipd.exe detach --busid "$busid" >/dev/null 2>&1 || return 1
  sleep 2
  echo "[usb] usbipd attach --wsl $busid" >&2
  usbipd.exe attach --wsl --busid "$busid" >/dev/null 2>&1 || return 1
  sleep 3
  return 0
}

# Detach iPhone usbipd so Windows AMDS owns the cable (WDA install). No UAC.
# Never call this for Android — Android USB stays in WSL.
ios_usb_release_to_windows() {
  local busid state line
  ios_usb_owner_set windows
  if ! command -v usbipd.exe >/dev/null 2>&1; then
    return 1
  fi
  line="$(ios_usb_apple_line)"
  busid="$(printf '%s\n' "$line" | awk '{print $1}')"
  state="$(printf '%s\n' "$line" | awk '{print $NF}')"
  if [[ -z "$busid" || "$line" != *05ac:* ]]; then
    echo "[usb] iPhone tidak terlihat di usbipd" >&2
    return 1
  fi
  if [[ "$state" == "Attached" ]]; then
    echo "[usb] usbipd detach $busid (Windows AMDS / pasang WDA)" >&2
    usbipd.exe detach --busid "$busid" >/dev/null 2>&1 || true
    sleep 2
  fi
  echo "[usb] iPhone di Windows (bukan WSL)" >&2
  return 0
}

ios_usb_reset_libusb() {
  local spec
  if ! command -v usbreset >/dev/null 2>&1; then
    return 1
  fi
  for spec in 05ac:12a8 05ac:12ab; do
    if sudo -n usbreset "$spec" >/dev/null 2>&1; then
      echo "[usb] usbreset $spec" >&2
      sleep 2
      return 0
    fi
  done
  return 1
}

ios_usb_restart_muxd() {
  if sudo -n systemctl restart usbmuxd >/dev/null 2>&1; then
    echo "[usb] restart usbmuxd" >&2
    sleep 2
    return 0
  fi
  return 1
}

ios_usb_wait_lockdown() {
  local udid="${1:-}"
  local i
  for i in $(seq 1 20); do
    if ios_usb_lockdown_ok "$udid"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

ios_usb_recover() {
  local udid="${1:-${UDID:-}}"
  echo "[usb] pulihkan lockdownd (UDID=${udid:-unknown})" >&2
  ios_usb_kill_stale
  ios_usb_recycle_usbipd || true
  ios_usb_reset_libusb || true
  ios_usb_restart_muxd || true
  if [[ -n "$udid" ]] && command -v idevicepair >/dev/null 2>&1; then
    idevicepair -u "$udid" pair >/dev/null 2>&1 || true
  elif command -v idevicepair >/dev/null 2>&1; then
    idevicepair pair >/dev/null 2>&1 || true
  fi
  if ios_usb_wait_lockdown "$udid"; then
    echo "[usb] lockdownd OK" >&2
    return 0
  fi
  echo "[usb] lockdownd masih macet. Unlock iPhone, tap Trust." >&2
  echo "[usb] Lalu (butuh sudo / Admin):" >&2
  echo "  sudo usbreset 05ac:12a8 && sudo systemctl restart usbmuxd" >&2
  echo "  # atau cabut USB 5 detik, colok lagi; di Windows: usbipd detach/attach --wsl" >&2
  if ios_usb_mux_overflow; then
    echo "[usb] usbmuxd log: paket 65536 (IPA write). Jangan ulang AltServer di WSL usbipd." >&2
  fi
  return 1
}

ios_usb_owner_path() {
  printf '%s' "${SIKSIK_IPHONE_USB_OWNER:-/tmp/siksik-iphone-usb.owner}"
}

ios_usb_owner_get() {
  cat "$(ios_usb_owner_path)" 2>/dev/null | tr -d '\r' | awk 'NF{print $1; exit}'
}

ios_usb_owner_set() {
  printf '%s\n' "$1" > "$(ios_usb_owner_path)"
}

ios_usb_held_by_windows() {
  [[ "$(ios_usb_owner_get)" == "windows" ]]
}

ios_usb_apple_line() {
  usbipd.exe list 2>/dev/null | tr -d '\r' | awk '/05ac:12a8|05ac:12ab/ {print; exit}'
}

ios_usb_wait_lsusb() {
  local i
  for i in $(seq 1 12); do
    if ios_usb_iphone_in_lsusb; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# Shared = bound for usbipd but not in WSL. Attach does not need UAC.
ios_usb_attach_shared() {
  local line busid state
  if ! command -v usbipd.exe >/dev/null 2>&1; then
    return 1
  fi
  line="$(ios_usb_apple_line)"
  busid="$(printf '%s\n' "$line" | awk '{print $1}')"
  state="$(printf '%s\n' "$line" | awk '{print $NF}')"
  if [[ -z "$busid" ]]; then
    return 1
  fi
  if [[ "$state" == "Attached" ]]; then
    ios_usb_wait_lsusb
    return $?
  fi
  case "$state" in
    Shared|"(forced)") ;;
    *) return 1 ;;
  esac
  echo "[usb] usbipd attach --wsl $busid" >&2
  usbipd.exe attach --wsl --busid "$busid" >/dev/null 2>&1 || return 1
  ios_usb_wait_lsusb
}

ios_usb_ensure_wsl() {
  if ! ios_usb_is_wsl; then
    return 0
  fi
  if ios_usb_windows_mux; then
    return 0
  fi
  if ios_usb_held_by_windows; then
    echo "[usb] iPhone di Windows (WDA); skip attach WSL" >&2
    return 0
  fi
  if ios_usb_iphone_in_lsusb; then
    ios_usb_owner_set wsl
    return 0
  fi
  if ios_usb_attach_shared; then
    ios_usb_owner_set wsl
    echo "[usb] iPhone Attached ke WSL" >&2
    return 0
  fi
  echo "[usb] iPhone belum di WSL" >&2
  return 1
}

ios_usb_claim_wsl() {
  local claim line
  if ! ios_usb_is_wsl; then
    return 0
  fi
  ios_usb_owner_set wsl
  if ios_usb_iphone_in_lsusb; then
    return 0
  fi
  if ios_usb_attach_shared; then
    ios_usb_owner_set wsl
    return 0
  fi
  line="$(ios_usb_apple_line)"
  if [[ -z "$line" ]]; then
    echo "[usb] tidak ada iPhone di usbipd; skip bind (jangan sentuh bus Android)" >&2
    return 1
  fi
  claim="$_IOS_USB_SCRIPTS/iphone_usb_wsl_only.sh"
  if [[ -f "$claim" ]]; then
    bash "$claim" || true
  fi
  ios_usb_attach_shared || true
  if ios_usb_iphone_in_lsusb; then
    ios_usb_owner_set wsl
    return 0
  fi
  return 1
}
