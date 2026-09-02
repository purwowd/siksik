#!/usr/bin/env bash
# Sign + install WDA IPA ke iPhone via AltServer-Linux (Apple ID gratis).
# Prasyarat: usbmuxd, idevice_id, AltServer binary, HP USB paired + unlocked.
set -euo pipefail

WDA_DIR="${WDA_DIR:-$HOME/wda}"
DEFAULT_IPA_NODSYM="$WDA_DIR/WebDriverAgentRunner-nodsym.ipa"
DEFAULT_IPA="$WDA_DIR/WebDriverAgentRunner.ipa"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REPO_IPA="$REPO_ROOT/WebDriverAgentRunner.ipa"

# ani.sidestore.io sering timeout / SSL handshake di AltServer-Linux.
# HTTP dulu (tanpa TLS); HTTPS publik hanya fallback.
ios_anisette_reachable() {
  local url="$1"
  [[ -n "$url" ]] || return 1
  curl -fsS -o /dev/null --connect-timeout 3 --max-time 6 "$url" 2>/dev/null
}

ios_anisette_pick() {
  local url
  for url in \
    "http://127.0.0.1:6969" \
    "${ALTSERVER_ANISETTE_SERVER:-}" \
    "http://5.249.163.88:6969" \
    "https://anisette.wedotstud.io" \
    "https://ani.idevicehacked.com" \
    "https://ani3server.fly.dev" \
    "https://ani.sidestore.io"
  do
    [[ -n "$url" ]] || continue
    if ios_anisette_reachable "$url"; then
      printf '%s\n' "$url"
      return 0
    fi
  done
  printf '%s\n' "${ALTSERVER_ANISETTE_SERVER:-http://5.249.163.88:6969}"
}

IPA="${1:-}"
if [[ -z "$IPA" ]]; then
  if [[
    -f "$DEFAULT_IPA_NODSYM" &&
    ( ! -f "$DEFAULT_IPA" || "$DEFAULT_IPA_NODSYM" -nt "$DEFAULT_IPA" )
  ]]; then
    IPA="$DEFAULT_IPA_NODSYM"
  elif [[ -f "$DEFAULT_IPA" ]]; then
    IPA="$DEFAULT_IPA"
  elif [[ -f "$DEFAULT_IPA_NODSYM" ]]; then
    IPA="$DEFAULT_IPA_NODSYM"
  elif [[ -f "$REPO_IPA" ]]; then
    IPA="$REPO_IPA"
  fi
fi

if [[ -z "$IPA" || ! -f "$IPA" ]]; then
  echo "Usage: $0 [/path/to/WebDriverAgentRunner.ipa]"
  echo "Env: APPLE_ID, APPLE_ID_PASSWORD  (wajib)"
  echo "     ALTSERVER_BIN / WDA_DIR (default: \$HOME/wda)"
  echo "     ALTSERVER_ANISETTE_SERVER (auto: HTTP anisette yang merespons)"
  exit 2
fi

if [[ -f "${REPO_ROOT:-}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
elif [[ -f "$HOME/ios-media-puller/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HOME/ios-media-puller/.env"
  set +a
fi

: "${APPLE_ID:?Set APPLE_ID}"
: "${APPLE_ID_PASSWORD:?Set APPLE_ID_PASSWORD (app-specific password jika 2FA)}"

export ALTSERVER_ANISETTE_SERVER
ALTSERVER_ANISETTE_SERVER="$(ios_anisette_pick)"

if [[ -n "${ALTSERVER_BIN:-}" ]]; then
  AS="$ALTSERVER_BIN"
elif [[ -x "$WDA_DIR/AltServer" ]]; then
  AS="$WDA_DIR/AltServer"
elif [[ -x ./AltServer ]]; then
  AS=./AltServer
elif command -v AltServer >/dev/null 2>&1; then
  AS=AltServer
else
  echo "AltServer tidak ditemukan. Unduh dari:"
  echo "  https://github.com/NyaMisty/AltServer-Linux/releases"
  echo "Lalu: chmod +x \$HOME/wda/AltServer  atau set ALTSERVER_BIN=/path/ke/AltServer"
  exit 2
fi

if ! command -v idevice_id >/dev/null 2>&1; then
  echo "idevice_id tidak ada. Install: sudo apt install libimobiledevice-utils"
  exit 2
fi

# shellcheck source=ios_usb.sh
source "$REPO_ROOT/ios_automator/scripts/ios_usb.sh"

ios_usb_kill_stale

UDID="${UDID:-$(idevice_id -l | head -1)}"
if [[ -z "$UDID" ]]; then
  echo "Tidak ada device. Colok USB, unlock, Trust, lalu cek: idevice_id -l"
  exit 2
fi

if ios_usb_wsl_direct && [[ "${IOS_ALLOW_WSL_USBMUX_IPA:-0}" != "1" ]]; then
  ios_usb_print_wsl_ipa_block
  exit 5
fi

if ! ios_usb_lockdown_ok "$UDID"; then
  if ios_usb_windows_mux; then
    echo "[install] lockdownd belum siap lewat mux Windows — pair / Trust, jangan attach ke WSL." >&2
  else
    echo "[install] lockdownd macet — pulihkan USB sebelum AltServer." >&2
    if ! ios_usb_recover "$UDID"; then
      echo "[install] USB belum sehat. Jangan tulis IPA sampai ideviceinfo berhasil." >&2
      exit 6
    fi
  fi
fi

# AltServer ldid crash kalau IPA masih berisi *.dSYM — strip otomatis
work_ipa="$IPA"
tmp_dir=""
cleanup() {
  if [[ -n "$tmp_dir" && -d "$tmp_dir" ]]; then
    rm -rf "$tmp_dir"
  fi
}
trap cleanup EXIT

if unzip -l "$IPA" 2>/dev/null | grep -qi '\.dSYM/'; then
  echo "[install] IPA berisi dSYM → strip dulu"
  tmp_dir="$(mktemp -d /tmp/wda-ipa.XXXXXX)"
  unzip -q "$IPA" -d "$tmp_dir"
  find "$tmp_dir" -type d -name '*.dSYM' -exec rm -rf {} + 2>/dev/null || true
  (cd "$tmp_dir" && zip -qr "$tmp_dir/app.ipa" Payload)
  work_ipa="$tmp_dir/app.ipa"
  # simpan salinan nodsym di WDA_DIR untuk run berikutnya
  mkdir -p "$WDA_DIR"
  cp "$work_ipa" "$DEFAULT_IPA_NODSYM"
  echo "[install] saved $DEFAULT_IPA_NODSYM"
fi

echo "[install] UDID=$UDID"
echo "[install] IPA=$work_ipa"
echo "[install] AltServer=$AS"
echo "[install] anisette=$ALTSERVER_ANISETTE_SERVER"

run_altserver_sign() {
  echo
  echo "════════════════════════════════════════════════════════════════"
  echo "  VERIFIKASI APPLE ID"
  echo "  Lihat kode 6 digit di layar iPhone → ketik di sini → Enter"
  echo "  (AltServer sering TIDAK menampilkan prompt — langsung ketik saja)"
  echo "════════════════════════════════════════════════════════════════"
  echo

  local log rc
  log="$(mktemp /tmp/altserver-wda.XXXXXX.log)"
  rc=0
  set +e
  if [[ "${IOS_ALTSERVER_STDIN_PIPE:-0}" == "1" ]]; then
    "$AS" -u "$UDID" -a "$APPLE_ID" -p "$APPLE_ID_PASSWORD" "$work_ipa" 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
  elif [[ -t 0 ]]; then
    "$AS" -u "$UDID" -a "$APPLE_ID" -p "$APPLE_ID_PASSWORD" "$work_ipa" 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
  elif [[ -r /dev/tty ]]; then
    # Pipeline/background: paksa stdin+stdout ke terminal asli
    "$AS" -u "$UDID" -a "$APPLE_ID" -p "$APPLE_ID_PASSWORD" "$work_ipa" < /dev/tty > /dev/tty 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
  else
    echo "[install] ERROR: butuh SATRIA Siapkan iPhone (kode 6 digit) atau terminal interaktif." >&2
    echo "[install] Jalankan dari UI operator, atau:" >&2
    echo "  bash $REPO_ROOT/ios_automator/scripts/install_wda_altserver.sh" >&2
    exit 3
  fi
  set -e

  if grep -qiE 'Failed to write|Could not install|Incorrect verification|has been locked|Error: com\.rileytestut\.AltServer' "$log"; then
    echo "[install] GAGAL memasang WDA (AltServer error). IPA tidak ada di iPhone." >&2
    if grep -qiE 'Failed to write app data|error connecting to the device' "$log"; then
      echo "[install] Gagal di salin USB, bukan Apple ID. lockdownd biasanya sudah macet." >&2
      if ios_usb_mux_overflow; then
        echo "[install] usbmuxd: paket 65536 — path WSL usbipd tidak bisa install IPA." >&2
        ios_usb_print_wsl_ipa_block
      fi
      ios_usb_recover "$UDID" || true
    fi
    rm -f "$log"
    exit 4
  fi
  if [[ "$rc" -ne 0 ]]; then
    echo "[install] AltServer exit $rc" >&2
    rm -f "$log"
    exit "$rc"
  fi
  rm -f "$log"
}

echo
echo "[install] Pakai app-specific password di .env agar jarang diminta kode."
echo "[install] Setelah sukses: Settings → VPN & Device Management → Trust developer"
echo

run_altserver_sign

if command -v ideviceinstaller >/dev/null 2>&1; then
  set +e
  ios_usb_wda_status "$UDID"
  wda_st=$?
  set -e
  if [[ "$wda_st" -eq 3 ]]; then
    echo "[install] AltServer selesai, tapi lockdownd macet — bukan bukti WDA absen." >&2
    ios_usb_recover "$UDID" || true
    set +e
    ios_usb_wda_status "$UDID"
    wda_st=$?
    set -e
  fi
  if [[ "$wda_st" -eq 3 ]]; then
    echo "[install] USB masih macet. Pulihkan, lalu: ideviceinstaller -l" >&2
    echo "  bash $REPO_ROOT/ios_automator/scripts/recover_ios_lockdown.sh" >&2
    exit 6
  fi
  if [[ "$wda_st" -eq 1 ]]; then
    echo "[install] GAGAL: AltServer selesai tanpa error teks, tapi WDA tidak ada di device." >&2
    exit 4
  fi
fi

echo
echo "[install] Installation selesai — WDA terdeteksi di iPhone."
echo "[install] Di iPhone (wajib sekali jika belum):"
echo "  Settings → General → VPN & Device Management → Trust Apple ID kamu"
echo "  Developer Mode: di-enable otomatis oleh script (atau manual di Settings)"
echo
