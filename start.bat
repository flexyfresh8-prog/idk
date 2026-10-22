@echo off
REM  Discord AI Bot - simple launcher
cd /d "%~dp0"
python -c "import flask" 2>nul || python -m pip install -r requirements.txt
python -u app.py
pause
