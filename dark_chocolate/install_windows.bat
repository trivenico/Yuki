@echo off
py -3 -m venv .venv
if errorlevel 1 exit /b 1
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -r dark_chocolate\requirements.txt
if errorlevel 1 exit /b 1
echo Installed. Start with: .venv\Scripts\activate.bat ^&^& python dark_chocolate\run_dark_chocolate.py
