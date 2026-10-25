@echo off
title Discord AI bot
cd /d "%~dp0"

REM ==== one button: checks python, fixes discord packages, runs ====

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

REM verify the SELF-BOT fork is really installed (not the junk 'discord' placeholder
REM or the official discord.py - both shadow the fork and break discord.Intents)
python -c "import discord; exit(0 if hasattr(discord,'Intents') and hasattr(discord,'Client') else 1)" 2>nul
if errorlevel 1 (
  echo  installing / fixing discord.py-self ...
  python -m pip uninstall -y discord discord.py >nul 2>nul
  python -m pip install --quiet --upgrade discord.py-self
)

python -c "import openai, dotenv" 2>nul || python -m pip install --quiet --upgrade openai python-dotenv

python -u bot.py

echo.
echo  The bot stopped. If it crashed, just double-click start.bat again.
pause
