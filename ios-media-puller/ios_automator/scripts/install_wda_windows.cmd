@echo off
setlocal EnableExtensions
title SATRIA - pasang WDA (USB Windows)

if /I not "%OS%"=="Windows_NT" (
  echo ERROR: Double-click file ini di Explorer Windows, bukan dari bash WSL.
  pause
  exit /b 1
)

cd /d "%~dp0" 2>nul
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_wda_windows.ps1" %*
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" if "%SIKSIK_WDA_INSTALL_WAIT_ENTER%"=="1" (
  echo.
  echo Exit code %EC%
  pause
)
endlocal & exit /b %EC%
