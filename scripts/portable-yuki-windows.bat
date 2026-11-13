@echo off
setlocal
set "YUKI_ROOT=%~dp0.."
for %%I in ("%YUKI_ROOT%") do set "YUKI_ROOT=%%~fI"
if not defined YUKI_HOME set "YUKI_HOME=%YUKI_ROOT%\YukiData"
cd /d "%YUKI_ROOT%"
if exist "%YUKI_ROOT%\.venv\Scripts\python.exe" (
  set "YUKI_PYTHON=%YUKI_ROOT%\.venv\Scripts\python.exe"
) else (
  set "YUKI_PYTHON=python"
)
"%YUKI_PYTHON%" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Check the runtime selected by this launcher.
  echo No software was installed.
  exit /b 1
)
"%YUKI_PYTHON%" -c "import yaml" >nul 2>nul
if errorlevel 1 (
  echo Yuki dependencies are missing. On a trusted computer, create a virtual environment and install requirements.txt.
  echo See docs/PORTABLE_SSD.md. This launcher does not install packages automatically.
  exit /b 2
)
"%YUKI_PYTHON%" scripts\portable_preflight.py
if errorlevel 1 exit /b 2
"%YUKI_PYTHON%" main.py
