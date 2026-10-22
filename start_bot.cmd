@echo off
REM Starts the dashboard + Discord bot in this window (visible).
REM The bot also starts on its own - AUTO_START=1 - so you only need this when
REM you want to watch the logs.
cd /d "%~dp0"
if exist ".venv2\Scripts\python.exe" (
  ".venv2\Scripts\python.exe" -u app.py
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -u app.py
) else (
  python -u app.py
)
pause
