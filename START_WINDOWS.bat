@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 or newer from python.org, then run this file again.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
if not exist .env .venv\Scripts\python.exe configure.py
if errorlevel 1 goto fail
.venv\Scripts\python.exe bot.py
pause
exit /b
:fail
echo Setup failed. Read the error above.
pause
exit /b 1
