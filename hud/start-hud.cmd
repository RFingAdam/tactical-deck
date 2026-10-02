@echo off
rem Starts the Protocol HUD server in the background (no console window).
cd /d "%~dp0"
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
  echo Protocol HUD is already running.
) else (
  start "" pythonw "%~dp0server.py"
  echo Protocol HUD started.
)
start "" http://127.0.0.1:8790/control
