@echo off
cd /d "%~dp0"
if exist .env for /f "usebackq tokens=*" %%a in (".env") do set "%%a"
python bot.py
pause
