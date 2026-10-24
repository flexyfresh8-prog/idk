@echo off
title Discord AI bot
cd /d "%~dp0"

REM ==== one button: checks python, installs deps, runs. nothing else to touch ====

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python isn't installed yet.
  echo  Opening the download page - install it, CHECK "Add python.exe to PATH",
  echo  then double-click start.bat again.
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import discord" 2>nul || python -m pip install --quiet --upgrade discord.py-self openai python-dotenv

python -u bot.py

echo.
echo  The bot stopped. If it crashed, just double-click start.bat again.
pause
