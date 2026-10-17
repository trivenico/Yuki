#!/bin/sh
set -eu
YUKI_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export YUKI_HOME="${YUKI_HOME:-$YUKI_ROOT/YukiData}"
cd "$YUKI_ROOT"
YUKI_PYTHON="$YUKI_ROOT/.venv/bin/python"
if [ ! -x "$YUKI_PYTHON" ]; then
  YUKI_PYTHON="$(command -v python3 || true)"
fi
if [ -z "$YUKI_PYTHON" ]; then
  echo "Python 3 is not installed. This launcher does not install software." >&2
  exit 1
fi
if ! "$YUKI_PYTHON" -c 'import yaml' >/dev/null 2>&1; then
  echo "Yuki dependencies are missing. On a trusted computer, create a virtual environment and install requirements.txt." >&2
  echo "See docs/PORTABLE_SSD.md. This launcher does not install packages automatically." >&2
  exit 2
fi
exec "$YUKI_PYTHON" main.py
