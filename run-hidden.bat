@echo off
REM ============================================================
REM  Starts the Discord AI bot with NO console window.
REM  Logs: logs\supervisor.log and logs\app.log
REM ============================================================
cd /d "%~dp0"

if exist ".venv2\Scripts\pythonw.exe" (
    start "" ".venv2\Scripts\pythonw.exe" "%~dp0supervisor.py"
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0supervisor.py"
) else (
    start "" pythonw "%~dp0supervisor.py"
)
exit /b 0
