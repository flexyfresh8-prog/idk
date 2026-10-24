@echo off
REM  Discord AI bot - double-click and it runs.
cd /d "%~dp0"
python -c "import flask" 2>nul || python -m pip install discord.py-self openai python-dotenv --quiet
python -u bot_simple.py
pause
