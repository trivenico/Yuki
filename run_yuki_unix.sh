#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Yuki's virtual environment is missing. Run: sh install_unix.sh"
  exit 1
fi
exec .venv/bin/python yuki_cli.py start
