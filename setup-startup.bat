@echo off
REM ============================================================
REM  Discord AI Bot - auto-start installer
REM
REM  Registers a Windows scheduled task that starts the supervisor at every
REM  logon. The supervisor keeps the dashboard (and the bot) alive forever,
REM  restarting them if they ever die.
REM
REM  No administrator rights needed. Run it once - it replaces any previous
REM  installation.
REM ============================================================
setlocal enabledelayedexpansion

set "BOT_DIR=%~dp0"
set "BOT_DIR=%BOT_DIR:~0,-1%"
set "TASK_NAME=Discord AI Bot"

echo.
echo  Installing auto-start for the Discord AI Bot
echo  --------------------------------------------
echo  Folder: %BOT_DIR%
echo.

REM --- 1. Pick the interpreter that actually has the dependencies -----------
set "PYTHON="
if exist "%BOT_DIR%\.venv2\Scripts\pythonw.exe" set "PYTHON=%BOT_DIR%\.venv2\Scripts\pythonw.exe"
if not defined PYTHON if exist "%BOT_DIR%\.venv\Scripts\pythonw.exe" set "PYTHON=%BOT_DIR%\.venv\Scripts\pythonw.exe"

if not defined PYTHON (
    echo  ERROR: no virtualenv found.
    echo         Create it first:
    echo             python -m venv .venv2
    echo             .venv2\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo  Using: %PYTHON%

REM --- 2. Sanity check: does that interpreter have what the app needs? -----
"%PYTHON%" -c "import discord, openai, flask" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: that Python is missing dependencies.
    echo         Run:
    echo             %PYTHON% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo  Dependencies: OK

REM --- 3. Remove any old task / startup entry ------------------------------
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /delete /tn "DiscordSelfbot" /f >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Discord-Bot-startup.bat" >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DiscordSelfbot.lnk" >nul 2>&1

REM --- 4. Create the scheduled task -----------------------------------------
REM  /sc onlogon  - start every time this user logs in
REM  pythonw      - no console window
REM  supervisor.py - restarts the app if it dies
schtasks /create /tn "%TASK_NAME%" /tr "\"%PYTHON%\" \"%BOT_DIR%\supervisor.py\"" /sc onlogon /f >nul 2>&1

if errorlevel 1 (
    echo.
    echo  Could not register the scheduled task - falling back to the Startup folder.
    copy /Y "%BOT_DIR%\startup-hidden.bat" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Discord-Bot-startup.bat" >nul
    if errorlevel 1 (
        echo  ERROR: that failed too. Run "%BOT_DIR%\startup-hidden.bat" by hand instead.
        echo.
        pause
        exit /b 1
    )
    echo  Added to the Startup folder instead.
) else (
    echo  Scheduled task "%TASK_NAME%" registered.
)

REM --- 5. Start it right now so there is no waiting -------------------------
schtasks /run /tn "%TASK_NAME%" >nul 2>&1

echo.
echo  DONE.
echo   * The bot now starts automatically at every logon, hidden.
echo   * If it ever crashes, the supervisor restarts it automatically.
echo   * Logs:     %BOT_DIR%\logs\supervisor.log
echo                %BOT_DIR%\logs\app.log
echo   * Dashboard: http://localhost:5000
echo.
echo  To remove the auto-start:
echo     schtasks /delete /tn "%TASK_NAME%" /f
echo.
pause
