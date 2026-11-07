#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
python3 scripts/install_local.py
exec "$ROOT/.venv/bin/python" scripts/launch_local.py "$@"
