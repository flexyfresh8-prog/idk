@echo off
REM ============================================================
REM  auto-start.bat  -  Discord AI Self-Bot auto-start installer
REM  ------------------------------------------------------------------
REM
REM  WHAT IT DOES:
REM    1. Verifies you launched it as Administrator.
REM    2. Verifies a Python virtualenv exists (.venv2 or .venv) and has
REM       the project dependencies installed.
REM    3. Removes any previous scheduled task / Startup folder entry.
REM    4. Registers a Windows scheduled task that starts the bot
REM       (via supervisor.py + pythonw) every time you log on.
REM    5. Starts the task immediately.
REM
REM  WHY ADMIN:
REM    The scheduled task is registered with /rl highest (elevated) so
REM    the bot survives UAC prompts and runs without a visible console.
REM    Without admin, schtasks /create silently fails and the bot will
REM    not auto-start on next logon.
REM
REM  TO UNINSTALL:
REM    schtasks /delete /tn "Discord AI Bot" /f
REM    (or just re-run this script, which overwrites the task.)
REM ============================================================
setlocal enabledelayedexpansion

set "BOT_DIR=%~dp0"
set "BOT_DIR=%BOT_DIR:~0,-1%"
set "TASK_NAME=Discord AI Bot"

echo.
echo  ============================================================
echo   Discord AI Bot  -  auto-start installer
echo  ============================================================
echo   Project folder: %BOT_DIR%
echo   Task name:      %TASK_NAME%
echo  ============================================================
echo.

REM --- 1. Check for Administrator privileges -------------------------------
net session >nul 2>&1
if errorlevel 1 (
    echo  ERROR: This script must be run as Administrator.
    echo.
    echo  Right-click auto-start.bat -> "Run as administrator".
    echo  (Without admin, schtasks /create silently fails and the bot
    echo   will not auto-start on next logon.)
    echo.
    pause
    exit /b 1
)
echo  [OK] Running as Administrator.

REM --- 2. Pick the Python interpreter (prefer pythonw.exe — no console) ----
set "PYTHON="
if exist "%BOT_DIR%\.venv2\Scripts\pythonw.exe" set "PYTHON=%BOT_DIR%\.venv2\Scripts\pythonw.exe"
if not defined PYTHON if exist "%BOT_DIR%\.venv\Scripts\pythonw.exe"  set "PYTHON=%BOT_DIR%\.venv\Scripts\pythonw.exe"

if not defined PYTHON (
    echo.
    echo  ERROR: No virtualenv found in %BOT_DIR%.
    echo         Create one first:
    echo             cd /d "%BOT_DIR%"
    echo             python -m venv .venv2
    echo             .venv2\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo  [OK] Python: %PYTHON%

REM --- 3. Verify the dependencies are installed ----------------------------
"%PYTHON%" -c "import discord, openai, flask" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: That Python is missing dependencies (discord, openai, flask).
    echo         Install them:
    echo             "%PYTHON%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo  [OK] Dependencies are installed.

REM --- 4. Make sure the logs folder exists (supervisor writes there) -------
if not exist "%BOT_DIR%\logs" mkdir "%BOT_DIR%\logs" >nul 2>&1

REM --- 5. Kill any bot process that is currently running -------------------
echo.
echo  Stopping any existing bot process...
taskkill /im pythonw.exe /f >nul 2>&1
taskkill /im python.exe  /f >nul 2>&1
REM     Wait a moment for the OS to release the lock on the .bot.lock file.
timeout /t 2 /nobreak >nul

REM --- 6. Remove any previous install ------------------------------------
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /delete /tn "DiscordSelfbot" /f >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Discord-Bot-startup.bat" >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DiscordSelfbot.lnk"      >nul 2>&1

REM --- 7. Register the scheduled task ------------------------------------
REM    /sc onlogon      - start every time any user logs in
REM    /rl highest      - run with highest privileges (survives UAC)
REM    /ru              - run as the current user (no password needed for
REM                       logon tasks; SYSTEM would not see your network
REM                       drives or your user .env so do NOT use it)
REM    /tr              - what to actually run: pythonw.exe supervisor.py
REM                       (supervisor.py restarts app.py if it ever dies)
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON%\" \"%BOT_DIR%\supervisor.py\"" ^
    /sc onlogon ^
    /rl highest ^
    /ru "%USERNAME%" ^
    /f >nul 2>&1

if errorlevel 1 (
    echo.
    echo  WARNING: Could not register the scheduled task.
    echo           Falling back to the Startup folder. The bot will still
    echo           auto-start on logon, just not as an elevated task.
    copy /Y "%BOT_DIR%\startup-hidden.bat" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Discord-Bot-startup.bat" >nul
    if errorlevel 1 (
        echo  ERROR: Startup folder fallback also failed.
        echo             Manually run: "%BOT_DIR%\startup-hidden.bat"
        echo.
        pause
        exit /b 1
    )
    echo  [OK] Added to the Startup folder (fallback).
) else (
    echo  [OK] Scheduled task "%TASK_NAME%" registered.
)

REM --- 8. Configure the task to auto-restart on failure (Task Scheduler XML) -
REM    schtasks /create cannot set "restart on failure" directly; we patch it
REM    via the XML interface. This is a no-op if it fails (the supervisor
REM    already handles in-process restarts, this just adds OS-level recovery).
powershell -NoProfile -Command "$t = Get-ScheduledTask -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue; if ($t) { $t.Settings.RestartOnFailure = $true; $t.Settings.RestartInterval = 'PT1M'; $t.Settings.ExecutionTimeLimit = 'PT0S'; Set-ScheduledTask -TaskName '%TASK_NAME%' -Settings $t.Settings -ErrorAction SilentlyContinue | Out-Null }" >nul 2>&1

REM --- 9. Start it right now so there's no waiting -------------------------
schtasks /run /tn "%TASK_NAME%" >nul 2>&1
echo  [OK] Task started.

echo.
echo  ============================================================
echo   DONE!
echo  ============================================================
echo   * The bot will auto-start whenever you log in to Windows.
echo   * It runs hidden (pythonw) - no console window.
echo   * The supervisor restarts app.py if it ever crashes.
echo   * A heartbeat watchdog inside app.py force-reconnects the
echo     Discord gateway if it goes silently stale.
echo.
echo   Logs:        %BOT_DIR%\logs\supervisor.log
echo                %BOT_DIR%\logs\app.log
echo   Dashboard:   http://localhost:5000
echo.
echo   To UNINSTALL:
echo      schtasks /delete /tn "%TASK_NAME%" /f
echo.
echo  ============================================================
pause
endlocal
