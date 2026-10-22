@echo off
REM Removes the Windows auto-start entry (scheduled task + startup shortcut).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Action Uninstall
pause
