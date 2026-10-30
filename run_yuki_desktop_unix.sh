#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Yuki's virtual environment is missing. Run: sh install_unix.sh"
  exit 1
fi
if .venv/bin/python yuki_cli.py setup; then
  :
else
  echo "First-run setup did not complete; continuing to the Yuki desktop."
fi
exec .venv/bin/python yuki_cli.py desktop
