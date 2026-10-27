#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

echo
echo "=========================================="
echo " Yuki AI Assistant - macOS/Linux Setup"
echo "=========================================="
echo

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "ERROR: Python 3.10 or newer was not found."
  echo "Install Python 3.10+ and run this script again."
  exit 1
fi

"$PYTHON" --version
if [ ! -x ".venv/bin/python" ]; then
  echo "[1/5] Creating isolated virtual environment..."
  "$PYTHON" -m venv .venv
else
  echo "[1/4] Existing virtual environment found."
fi

echo "[2/5] Updating pip..."
if ! .venv/bin/python -m pip install --upgrade pip; then
  echo "WARNING: pip upgrade failed; continuing after pip self-upgrade failure; dependency installation below must still succeed."
fi

echo "[3/5] Checking core dependencies..."
.venv/bin/python -m pip install -r requirements.txt

echo "[4/5] Installing the desktop supervision UI..."
.venv/bin/python -m pip install -r requirements-ui.txt
echo "[5/5] Running Yuki test suite..."
.venv/bin/python -m unittest discover -s tests -v

echo
echo "=========================================="
echo " Setup completed successfully."
echo " Start Yuki with: ./run_yuki_unix.sh"
echo "=========================================="
