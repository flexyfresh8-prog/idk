@echo off
REM ============================================================
REM  Discord AI Bot - hidden starter
REM
REM  Starts the supervisor, which keeps the dashboard and the bot running
REM  (and restarts them if they die). Uses pythonw, so no console window.
REM
REM  This file is what gets copied into the Windows Startup folder, and it is
REM  also what the scheduled task runs.
REM ============================================================
cd /d "%~dp0"

set "PYTHON="
if exist "%~dp0.venv2\Scripts\pythonw.exe" set "PYTHON=%~dp0.venv2\Scripts\pythonw.exe"
if not defined PYTHON if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYTHON=%~dp0.venv\Scripts\pythonw.exe"

if not defined PYTHON (
    REM No virtualenv: try the system Python so the error ends up in the log
    REM instead of vanishing.
    if exist "%~dp0logs" (
        echo %date% %time% No virtualenv found - falling back to the system Python.> "%~dp0logs\startup-error.log"
    )
    start "" pythonw "%~dp0supervisor.py"
    exit /b 0
)

start "" "%PYTHON%" "%~dp0supervisor.py"
exit /b 0
