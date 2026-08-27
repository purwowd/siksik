@echo off
setlocal EnableExtensions
title SATRIA desktop - Windows UI (bukan WSL)

rem Jangan jalankan lewat "wsl ..."; file ini untuk CMD/PowerShell/Explorer Windows.
if defined WSL_DISTRO_NAME (
  echo ERROR: Buka script ini di Windows, bukan dari dalam WSL.
  echo Double-click: C:\siksik\scripts\start_desktop_windows.cmd
  pause
  exit /b 1
)

rem Tetap di folder scripts; PS1 yang mencari repo (C:\siksik, env, \\wsl$, ...)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_desktop_windows.ps1" %*
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo Exit code %EC% - tekan tombol untuk tutup.
  pause
)
endlocal & exit /b %EC%
