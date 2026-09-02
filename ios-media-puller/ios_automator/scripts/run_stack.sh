#!/usr/bin/env bash
# Start iOS automation stack: tunnel → ensure WDA → port forward (Linux / iOS 17+).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WDA_DIR="${WDA_DIR:-$HOME/wda}"
TUNNEL_INFO_PORT="${GO_IOS_TUNNEL_INFO_PORT:-60105}"
WDA_PORT="${WDA_PORT:-8100}"
UDID="${UDID:-$(idevice_id -l 2>/dev/null | head -1)}"

export GO_IOS_TUNNEL_INFO_PORT="${TUNNEL_INFO_PORT}"
export WDA_PORT
export PATH="${HOME}/.local/bin:${PATH}"
export ROOT

# shellcheck disable=SC1091
[[ -f "$ROOT/ios_automator/scripts/run_log.sh" ]] && source "$ROOT/ios_automator/scripts/run_log.sh"
[[ -f "$ROOT/.env" ]] && set -a && source "$ROOT/.env" && set +a

log_stack_event() {
  if declare -F run_log >/dev/null 2>&1; then
    run_log STACK "$*"
  fi
}

extract_wda_bundle() {
  local raw="$1"
  grep -oE '([A-Za-z0-9-]+\.)+WebDriverAgentRunner(\.[A-Za-z0-9._-]+)*' <<<"$raw" | tail -1
}

puller_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
  else
    command -v python3
  fi
}

STACK_STATE_DIR="${IOS_STACK_STATE_DIR:-/tmp/ios-media-puller-stack}"
STACK_UDID_FILE="${STACK_STATE_DIR}/udid"

dev_image_mounted() {
  ios image list ${UDID:+--udid "$UDID"} \
    --tunnel-info-port="$TUNNEL_INFO_PORT" 2>&1 \
    | grep -q 'image signature'
}

ensure_usb_pair() {
  if ! command -v idevicepair >/dev/null 2>&1; then
    echo "[stack] idevicepair tidak ada — skip cek Trust"
    return 0
  fi
  if idevicepair -u "$UDID" validate >/dev/null 2>&1; then
    echo "[stack] USB pairing OK"
    return 0
  fi
  echo "[stack] USB belum di-Trust — kirim permintaan pairing (ketuk Trust di iPhone)…"
  log_stack_event "USB pair / Trust"
  idevicepair -u "$UDID" pair >/dev/null 2>&1 || true
  local i
  for i in $(seq 1 45); do
    if idevicepair -u "$UDID" validate >/dev/null 2>&1; then
      echo "[stack] USB pairing OK"
      return 0
    fi
    sleep 1
  done
  echo "[stack] iPhone belum Trust komputer ini. Unlock HP, ketuk Trust This Computer, lalu jalankan akuisisi lagi." >&2
  return 1
}

reset_stale_stack() {
  mkdir -p "$STACK_STATE_DIR"
  local prev=""
  if [[ -f "$STACK_UDID_FILE" ]]; then
    prev="$(tr -d '[:space:]' <"$STACK_UDID_FILE" || true)"
  fi
  if [[ "$prev" == "$UDID" ]]; then
    return 0
  fi
  if [[ -n "$prev" ]]; then
    echo "[stack] device berganti ($prev → $UDID) — reset tunnel & WDA"
    log_stack_event "device changed ${prev} → ${UDID}"
  else
    echo "[stack] belum ada state UDID — reset sisa tunnel/WDA lama"
  fi
  bash "$ROOT/ios_automator/scripts/stop_stack.sh" all || true
}

record_stack_udid() {
  mkdir -p "$STACK_STATE_DIR"
  printf '%s\n' "$UDID" >"$STACK_UDID_FILE"
}

ensure_dev_image() {
  # iOS 17+: tanpa Developer Disk Image ter-mount, dtservicehub/testmanagerd tidak
  # terdaftar di RSD, jadi runwda minta "device port 0" → connection refused →
  # DTX broken pipe dan WDA tidak pernah listen di :8100. Mount hilang setiap
  # iPhone reboot / ganti HP, jadi cek tiap akuisisi dan mount otomatis (butuh internet).
  if [[ "${IOS_ENSURE_DEV_IMAGE:-1}" != "1" && "${IOS_MOUNT_DEV_IMAGE:-0}" != "1" ]]; then
    return 0
  fi
  if dev_image_mounted; then
    echo "[stack] developer image sudah mounted"
    return 0
  fi
  echo "[stack] developer image belum mounted — auto-mount (bisa 30–90s, butuh internet)…"
  log_stack_event "mount developer disk image…"
  local mount_log="${IOS_MOUNT_LOG:-/tmp/ios-media-puller-mounter.log}"
  local py
  py="$(puller_python)"
  "$py" -m pymobiledevice3 mounter auto-mount >>"$mount_log" 2>&1 \
    || ios image auto ${UDID:+--udid "$UDID"} \
      --tunnel-info-port="$TUNNEL_INFO_PORT" >>"$mount_log" 2>&1 \
    || true
  # Daftar layanan RSD dibaca saat handshake tunnel; restart supaya layanan
  # developer yang baru muncul terlihat oleh runwda.
  bash "$ROOT/ios_automator/scripts/start_tunnel.sh" stop || true
  sleep 2
  bash "$ROOT/ios_automator/scripts/start_tunnel.sh" ensure
  if dev_image_mounted; then
    echo "[stack] developer image mounted"
    log_stack_event "developer image mounted"
    return 0
  fi
  echo "[stack] developer image gagal mount — cek $mount_log (butuh internet, iPhone unlock)." >&2
  log_stack_event "developer image gagal mount"
  return 1
}

wda_http_alive() {
  curl -sf --max-time 1 "http://127.0.0.1:${WDA_PORT}/status" >/dev/null 2>&1
}

start_wda() {
  local boot_wait="${IOS_WDA_BOOT_WAIT_SEC:-12}"
  if [[ "${IOS_FORCE_WDA_RESTART:-0}" != "1" ]] && wda_http_alive; then
    echo "[stack] WDA HTTP sudah OK"
    return 0
  fi
  pkill -f "ios runwda" 2>/dev/null || true
  pkill -f "ios forward.*${WDA_PORT}" 2>/dev/null || true
  sleep "${IOS_WDA_STOP_SLEEP_SEC:-0.5}"

  echo "[stack] starting runwda…"
  : >"${IOS_WDA_LOG:-/tmp/ios-media-puller-wda.log}"
  nohup ios runwda \
    --bundleid "$WDA_BUNDLE" \
    --testrunnerbundleid "$WDA_BUNDLE" \
    --xctestconfig WebDriverAgentRunner.xctest \
    --tunnel-info-port="$TUNNEL_INFO_PORT" \
    ${UDID:+--udid "$UDID"} >>"${IOS_WDA_LOG:-/tmp/ios-media-puller-wda.log}" 2>&1 &
  disown $! 2>/dev/null || true

  sleep 2
  echo "[stack] forwarding ${WDA_PORT}…"
  nohup ios forward \
    --tunnel-info-port="$TUNNEL_INFO_PORT" \
    ${UDID:+--udid "$UDID"} \
    "$WDA_PORT" "$WDA_PORT" >>"${IOS_WDA_LOG:-/tmp/ios-media-puller-wda.log}" 2>&1 &
  disown $! 2>/dev/null || true

  local i
  for i in $(seq 1 "$((boot_wait * 2))"); do
    if wda_http_alive; then
      echo "[stack] WDA HTTP ready on :${WDA_PORT} (~$((i / 2))s)"
      return 0
    fi
    sleep 0.5
  done
  echo "[stack] WDA belum ready dalam ${boot_wait}s — lanjut wait_wda_http" >&2
}

wait_wda_http() {
  local i
  local max="${IOS_WDA_HTTP_WAIT_SEC:-30}"
  for i in $(seq 1 "$((max * 2))"); do
    if wda_http_alive; then
      echo "[stack] WDA HTTP ready on :${WDA_PORT}"
      return 0
    fi
    sleep 0.5
  done
  echo "[stack] WDA tidak merespons di :${WDA_PORT}" >&2
  if [[ -f "${IOS_WDA_LOG:-/tmp/ios-media-puller-wda.log}" ]] \
    && grep -q 'cannot initiate a IDE session' \
      "${IOS_WDA_LOG:-/tmp/ios-media-puller-wda.log}" 2>/dev/null; then
    echo "[stack] Layanan XCTest tidak terjangkau — Developer Image kemungkinan belum mounted:" >&2
    echo "  ios image list --udid \$UDID --tunnel-info-port=${TUNNEL_INFO_PORT}" >&2
    echo "  .venv/bin/python -m pymobiledevice3 mounter auto-mount" >&2
  fi
  if [[ -f "${IOS_WDA_LOG:-/tmp/ios-media-puller-wda.log}" ]] \
    && grep -qE 'deviceprocesscontrolservice|could not get pid|Untrusted|not verified' \
      "${IOS_WDA_LOG:-/tmp/ios-media-puller-wda.log}" 2>/dev/null; then
    echo "[stack] Kemungkinan WDA belum di-Trust di iPhone:" >&2
    echo "  Settings → General → VPN & Device Management → Trust developer" >&2
    echo "  Lalu jalankan ulang: ./ios_automator/scripts/run_ig_profile.sh" >&2
  fi
  return 1
}

if ! command -v ios >/dev/null 2>&1; then
  echo "ios (go-ios) tidak ada di PATH. Install dulu." >&2
  exit 2
fi

if [[ -z "$UDID" ]]; then
  echo "Device tidak terdeteksi. Colok USB, unlock, Trust: idevice_id -l" >&2
  exit 2
fi

if [[ "${IOS_FORCE_WDA_RESTART:-0}" != "1" ]] && wda_http_alive; then
  echo "[stack] WDA HTTP sudah OK — skip pair/DDI (Automation Running)"
  record_stack_udid
  log_stack_event "WDA HTTP already ready on :${WDA_PORT}"
  echo "[stack] ready"
  exit 0
fi

reset_stale_stack
ensure_usb_pair

bash "$ROOT/ios_automator/scripts/start_tunnel.sh" ensure
log_stack_event "tunnel ready (:${TUNNEL_INFO_PORT})"

bash "$ROOT/ios_automator/scripts/ensure_developer_mode.sh" ensure
log_stack_event "developer mode OK"

ensure_dev_image

# Capture bundle ke stdout; log ensure-wda tetap ke stderr (terlihat di terminal)
WDA_BUNDLE="${WDA_BUNDLE:-$(bash "$ROOT/ios_automator/scripts/ensure_wda.sh")}"
WDA_BUNDLE="$(extract_wda_bundle "$WDA_BUNDLE")"
[[ -n "$WDA_BUNDLE" ]] || { echo "[stack] WDA_BUNDLE tidak terdeteksi" >&2; exit 2; }
export WDA_BUNDLE
echo "[stack] WDA_BUNDLE=$WDA_BUNDLE"
echo "[stack] UDID=$UDID"
log_stack_event "WDA bundle=${WDA_BUNDLE}"

bash "$ROOT/ios_automator/scripts/keep_screen_on.sh" restart || \
  bash "$ROOT/ios_automator/scripts/keep_screen_on.sh" start || true
start_wda
if ! wait_wda_http; then
  echo "[stack] WDA HTTP gagal — restart runwda sekali…" >&2
  pkill -f "ios runwda" 2>/dev/null || true
  pkill -f "ios forward.*${WDA_PORT}" 2>/dev/null || true
  sleep 1
  start_wda
  wait_wda_http
fi
record_stack_udid
log_stack_event "WDA HTTP ready on :${WDA_PORT}"
echo "[stack] ready"
