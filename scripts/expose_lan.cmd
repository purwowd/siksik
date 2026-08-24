@echo off
set "SCRIPT=%~dp0expose_lan.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"\"%SCRIPT%\"\"' -Wait"
