@echo off
REM ============================================================
REM  Starts the Discord AI bot (dashboard + bot) in this window.
REM
REM  The supervisor is used, so the app is restarted automatically if it
REM  crashes. Logs land in the logs\ folder.
REM ============================================================
cd /d "%~dp0"

if exist ".venv2\Scripts\python.exe" (
    ".venv2\Scripts\python.exe" -u supervisor.py
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -u supervisor.py
) else (
    echo Virtual environment not found. Run:
    echo     python -m venv .venv2
    echo     .venv2\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

pause
