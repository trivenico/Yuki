@echo off
setlocal
set "YUKI_ROOT=%~dp0.."
for %%I in ("%YUKI_ROOT%") do set "YUKI_ROOT=%%~fI"
if not defined YUKI_HOME set "YUKI_HOME=%USERPROFILE%\.yuki"
cd /d "%YUKI_ROOT%"
set "YUKI_PYTHON=%YUKI_ROOT%\.venv\Scripts\python.exe"
if not exist "%YUKI_PYTHON%" set "YUKI_PYTHON=python"
"%YUKI_PYTHON%" -c "import yaml; import tkinter" >nul 2>&1
if errorlevel 1 (
  echo Yuki desktop dependencies or Tk support are missing. See docs/DESKTOP_APP.md.
  exit /b 2
)
"%YUKI_PYTHON%" desktop_app.py
