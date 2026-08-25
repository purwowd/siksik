#!/usr/bin/env bash
# Install Tauri 2 Linux deps for WSL + WSLg (window appears on Windows desktop).
# Run once: bash scripts/setup_tauri_wsl.sh
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Jangan jalankan sebagai root; pakai user biasa + sudo." >&2
  exit 1
fi

echo "Installing Tauri/WebKitGTK packages (sudo password sekali)…"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential \
  curl \
  wget \
  file \
  libxdo-dev \
  libssl-dev \
  libgtk-3-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  patchelf \
  libwebkit2gtk-4.1-dev

echo ""
echo "OK. Lanjut desktop:"
echo "  . \"\$HOME/.cargo/env\""
echo "  # pastikan API sudah di :8000"
echo "  cd desktop && ./dev.sh"
echo "Jendela SATRIA akan muncul di desktop Windows (WSLg)."
