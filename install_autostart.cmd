@echo off
REM Installs the Windows auto-start entry: the dashboard + Discord bot start
REM automatically at every logon, hidden, and restart if they die.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Action Install
if errorlevel 1 (
  echo.
  echo Install failed. Try running this from a PowerShell window as Administrator.
  pause
)
