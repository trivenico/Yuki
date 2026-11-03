@echo off
setlocal
cd /d "%~dp0"
echo.
echo ==========================================
echo  Yuki AI Assistant - Windows Setup
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found.
  echo Install Python 3.10 or newer from https://www.python.org/downloads/
  echo During installation, enable "Add Python to PATH".
  exit /b 1
)

python -c "import sys; print('Detected Python:', sys.version.split()[0]); sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo ERROR: Yuki requires Python 3.10 or newer.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating isolated virtual environment...
  python -m venv .venv
  if errorlevel 1 exit /b 1
) else (
  echo [1/4] Existing virtual environment found.
)

echo [2/4] Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo WARNING: pip upgrade failed. Continuing with the existing pip; the required dependency installation below must still succeed.
)

echo [3/4] Checking declared dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: Dependency step failed.
  exit /b 1
)

echo [4/4] Running Yuki test suite...
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 (
  echo ERROR: Tests failed. Review the failure output above before using Yuki.
  exit /b 1
)

echo.
echo ==========================================
echo  Setup completed successfully.
echo  Start Yuki with: run_yuki_windows.bat
echo ==========================================
endlocal
