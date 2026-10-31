@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python Launcher was not found. Install Python 3.10 or newer from python.org and enable the launcher.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Yuki's isolated Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)

echo Checking Yuki dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo Starting Yuki Desktop...
".venv\Scripts\python.exe" yuki_desktop.py
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo Yuki could not start. Review the error above.
pause
exit /b 1
