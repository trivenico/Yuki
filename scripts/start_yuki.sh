#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
YUKI_VENV_DIR="${YUKI_VENV_DIR:-$ROOT/.venv}"
export YUKI_VENV_DIR
python3 scripts/install_local.py --venv-dir "$YUKI_VENV_DIR"
exec "$YUKI_VENV_DIR/bin/python" scripts/launch_local.py "$@"
