@echo off
setlocal EnableExtensions
title SATRIA - Allow Windows (Defender / portproxy)

if /I not "%OS%"=="Windows_NT" (
  echo ERROR: Jalankan dari Windows, bukan WSL.
  pause
  exit /b 1
)

cd /d "%~dp0" 2>nul
if errorlevel 1 pushd "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Meminta UAC Administrator - pilih Yes sekali...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0allow_satria_windows.ps1\"'"
  set "EC=%ERRORLEVEL%"
  endlocal & exit /b %EC%
)

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0allow_satria_windows.ps1" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
