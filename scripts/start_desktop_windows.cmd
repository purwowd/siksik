@echo off
setlocal EnableExtensions
title SATRIA desktop - Windows UI (bukan WSL)

rem Hanya tolak jika benar-benar Linux (bukan Windows yang inherit WSL_*).
if /I not "%OS%"=="Windows_NT" (
  echo ERROR: Buka shortcut SATRIA di desktop Windows, bukan dari shell WSL.
  pause
  exit /b 1
)

cd /d "%~dp0" 2>nul
if errorlevel 1 pushd "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_desktop_windows.ps1" %*
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo Exit code %EC% - tekan tombol untuk tutup.
  pause
)
endlocal & exit /b %EC%
