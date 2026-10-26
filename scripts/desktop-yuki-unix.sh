#!/bin/sh
set -eu
YUKI_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export YUKI_HOME="${YUKI_HOME:-$HOME/.yuki}"
cd "$YUKI_ROOT"
YUKI_PYTHON="$YUKI_ROOT/.venv/bin/python"
if [ ! -x "$YUKI_PYTHON" ]; then
  YUKI_PYTHON="$(command -v python3 || true)"
fi
if [ -z "$YUKI_PYTHON" ]; then
  echo "Python 3 is required. See docs/DESKTOP_APP.md." >&2
  exit 1
fi
if ! "$YUKI_PYTHON" -c 'import yaml; import tkinter' >/dev/null 2>&1; then
  echo "Yuki desktop dependencies or Tk support are missing. See docs/DESKTOP_APP.md." >&2
  exit 2
fi
exec "$YUKI_PYTHON" desktop_app.py
