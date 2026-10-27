@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Yuki's virtual environment is missing.
  echo Run install_windows.bat first.
  exit /b 1
)
".venv\Scripts\python.exe" yuki_cli.py setup
".venv\Scripts\python.exe" yuki_cli.py desktop
endlocal
