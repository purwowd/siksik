#!/usr/bin/env bash
# Buka PowerShell Administrator Windows (UAC) — alur ~20:30.
# USB dilepas ke AMDS dulu; kode 6 digit diketik di jendela PowerShell.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ios_usb.sh
source "$HERE/ios_usb.sh"
WIN_DIR="/mnt/c/Users/Admin/wda"
mkdir -p "$WIN_DIR"
cp -f "$HERE/install_wda_windows.ps1" "$WIN_DIR/install_wda_windows.ps1"
cp -f "$HERE/ensure_amds_windows.ps1" "$WIN_DIR/ensure_amds_windows.ps1"
if [[ -f "$HERE/iphone_usb_wsl_only.ps1" ]]; then
  cp -f "$HERE/iphone_usb_wsl_only.ps1" "$WIN_DIR/iphone_usb_wsl_only.ps1"
fi
# PowerShell 5.1: UTF-8 tanpa BOM merusak string (jendela UAC Yes lalu langsung tutup).
powershell.exe -NoProfile -Command \
  "\$p='C:\\Users\\Admin\\wda\\install_wda_windows.ps1'; \$c=Get-Content -Raw -Encoding UTF8 \$p; Set-Content -Encoding Unicode -NoNewline -Path \$p -Value \$c"
PS1="C:\\Users\\Admin\\wda\\install_wda_windows.ps1"

echo "[windows-wda] UAC Windows akan muncul — pilih Yes."
echo "[windows-wda] Kode 6 digit Apple: ketik di jendela PowerShell, lalu Enter."
echo "[windows-wda] USB tetap di Windows sampai Trust profil di SATRIA."

export SIKSIK_WDA_INSTALL_WAIT_ENTER=1
ios_usb_release_to_windows || true

set +e
powershell.exe -NoProfile -Command \
  "\$p = Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru -WorkingDirectory 'C:\\Users\\Admin\\wda' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\\Users\\Admin\\wda\\install_wda_windows.ps1'; if (\$null -eq \$p) { exit 1 }; exit \$p.ExitCode"
ec=$?
set -e

if [[ "$ec" -eq 0 ]]; then
  echo "[windows-wda] selesai. USB tetap di Windows — Trust profil, lalu Sudah di-Trust."
  exit 0
fi
echo "[windows-wda] install gagal (exit $ec). USB masih di Windows." >&2
echo "[windows-wda] Jangan tutup jendela PowerShell sampai selesai ketik kode." >&2
exit "$ec"
