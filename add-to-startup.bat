@echo off
REM ============================================================
REM  Adds the Discord AI Bot to your Windows Startup folder.
REM
REM  No administrator rights needed. This is the fallback for when a
REM  scheduled task cannot be registered - setup-startup.bat prefers the
REM  scheduled task and only uses this if it fails.
REM ============================================================
setlocal

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo.
echo  Adding the Discord AI Bot to your Startup folder...
echo.

if not exist "%STARTUP_FOLDER%" (
    echo  ERROR: Startup folder not found: %STARTUP_FOLDER%
    pause
    exit /b 1
)

copy /Y "%~dp0startup-hidden.bat" "%STARTUP_FOLDER%\Discord-Bot-startup.bat" >nul

if errorlevel 1 (
    echo  ERROR: could not copy the startup script.
    pause
    exit /b 1
)

echo  SUCCESS: the bot will start automatically at every logon.
echo.
echo  To remove it, delete this file:
echo    %STARTUP_FOLDER%\Discord-Bot-startup.bat
echo.
echo  To start it right now:
echo    "%~dp0startup-hidden.bat"
echo.
pause
