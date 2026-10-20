#!/usr/bin/env sh
set -eu
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r dark_chocolate/requirements.txt
printf '\nInstalled. Start with: . .venv/bin/activate && python dark_chocolate/run_dark_chocolate.py\n'
