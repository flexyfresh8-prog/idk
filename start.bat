@echo off
REM  Discord AI Bot - simple launcher
REM  Runs the Flask dashboard + Discord bot in one process.
REM  The bot auto-starts on launch (AUTO_START=1).
REM
REM  Dashboard: http://localhost:5000
REM  Stop: Ctrl+C in this window.
cd /d "%~dp0"
python -u app.py
pause
