@echo off
REM ============================================================
REM  start.bat  -  Discord AI Bot: one-shot setup + run
REM  ------------------------------------------------------------------
REM  Does everything:
REM    1. Finds Python on your system (py launcher or python.exe)
REM    2. Creates the .venv2 virtualenv if it does not exist
REM    3. Installs / updates all dependencies from requirements.txt
REM    4. Starts the supervisor (which starts app.py + the Discord bot)
REM
REM  Just double-click this file. First run takes ~30s for venv setup.
REM  Subsequent runs just start the bot.
REM
REM  Dashboard: http://localhost:5000
REM  Logs:      logs\supervisor.log, logs\app.log
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYTHON="

REM --- 1. Pick a venv interpreter that exists, else create one ----------
if exist ".venv2\Scripts\python.exe" (
    set "PYTHON=.venv2\Scripts\python.exe"
    goto :have_python
)
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    goto :have_python
)

REM --- 2. No venv yet. Find a system Python to create one with ---------
echo  No virtualenv found - creating one...
echo.

where py >nul 2>&1
if not errorlevel 1 (
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        echo  Creating .venv2 with py -3...
        py -3 -m venv .venv2
        if exist ".venv2\Scripts\python.exe" set "PYTHON=.venv2\Scripts\python.exe"
    )
)

if not defined PYTHON (
    where python >nul 2>&1
    if not errorlevel 1 (
        python --version >nul 2>&1
        if not errorlevel 1 (
            echo  Creating .venv2 with python...
            python -m venv .venv2
            if exist ".venv2\Scripts\python.exe" set "PYTHON=.venv2\Scripts\python.exe"
        )
    )
)

if not defined PYTHON (
    echo.
    echo  ERROR: Python is not installed or not in PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         Be sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

:have_python
echo  Using Python: %PYTHON%

REM --- 3. Make sure pip is the latest + dependencies are installed ------
if not exist ".venv2\Scripts\discord.py-self_installed.marker" (
    echo.
    echo  Installing dependencies ^(first run only^)...
    "%PYTHON%" -m pip install --upgrade pip >nul 2>&1
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo  ERROR: dependency install failed. See message above.
        pause
        exit /b 1
    )
    echo. > ".venv2\Scripts\discord.py-self_installed.marker"
    echo  Dependencies installed.
) else (
    REM Quick sanity check that deps are importable.
    "%PYTHON%" -c "import discord, openai, flask" >nul 2>&1
    if errorlevel 1 (
        echo  Dependencies look broken - reinstalling...
        "%PYTHON%" -m pip install -r requirements.txt
        if errorlevel 1 (
            echo  ERROR: reinstall failed.
            pause
            exit /b 1
        )
    )
)

REM --- 4. Make sure the logs folder exists (supervisor writes there) ----
if not exist "logs" mkdir logs >nul 2>&1

REM --- 5. Start the supervisor -----------------------------------------
echo.
echo  Starting the bot...
echo  Dashboard: http://localhost:5000
echo  Press Ctrl+C in this window to stop.
echo.
"%PYTHON%" -u supervisor.py

pause
endlocal
