#!/usr/bin/env bash
# Install and enable SATRIA API as a systemd --user service (WSL).
# After this: reboot Windows → WSL autostart task → API up without opening a terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$ROOT/scripts/systemd/satria-api.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/satria-api.service"
API_PORT="${SADT_API_PORT:-8000}"

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "ERROR: missing $UNIT_SRC" >&2
  exit 1
fi
if [[ ! -x "$ROOT/scripts/start_api.sh" ]]; then
  chmod +x "$ROOT/scripts/start_api.sh"
fi
if [[ ! -f "$ROOT/backend/.venv/bin/uvicorn" ]] && [[ ! -f "$ROOT/backend/.venv/bin/python" ]]; then
  echo "ERROR: backend/.venv belum siap" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR" "$ROOT/logs"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"

# Linger: user services start when the WSL distro boots (no interactive login).
if command -v loginctl >/dev/null 2>&1; then
  if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)" != "yes" ]]; then
    echo "Enable linger for $USER (may ask sudo once)…"
    if loginctl enable-linger "$USER" 2>/dev/null; then
      :
    elif sudo loginctl enable-linger "$USER"; then
      :
    else
      echo "WARNING: gagal enable-linger — service hanya jalan setelah login session WSL" >&2
    fi
  fi
fi

systemctl --user daemon-reload
systemctl --user enable satria-api.service

port_busy=0
if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :$API_PORT )" 2>/dev/null | grep -q ":$API_PORT"; then
    port_busy=1
  fi
elif command -v lsof >/dev/null 2>&1; then
  if lsof -ti:"$API_PORT" >/dev/null 2>&1; then
    port_busy=1
  fi
fi

if [[ "$port_busy" == "1" ]]; then
  echo ""
  echo "Port :$API_PORT sedang dipakai (kemungkinan ./start_poc.sh di terminal)."
  echo "Service sudah di-enable untuk boot berikutnya."
  echo "Sekarang: Ctrl+C di terminal start_poc, lalu:"
  echo "  systemctl --user start satria-api.service"
  echo ""
else
  systemctl --user restart satria-api.service
  sleep 2
  systemctl --user --no-pager --full status satria-api.service || true
fi

echo ""
echo "SATRIA API systemd user service terpasang."
echo "  status:  systemctl --user status satria-api"
echo "  log:     journalctl --user -u satria-api -f"
echo "  file:    $ROOT/logs/satria-api.service.log"
echo "  stop:    systemctl --user stop satria-api"
echo "  disable: systemctl --user disable --now satria-api"
echo ""
echo "Agar API ikut nyala setelah reboot Windows, jalankan sekali (Admin):"
echo "  C:\\siksik\\scripts\\allow_satria_windows.cmd"
echo "(mendaftarkan task logon yang membangunkan WSL + satria-api)"
