#!/usr/bin/env bash
# Create a timestamped local backup of Yuki's private workspace.
# Usage: sh scripts/daily_backup.sh [source_dir] [backup_dir]
set -eu
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
DATA_ROOT="${YUKI_HOME:-$REPO_ROOT}"
case "$DATA_ROOT" in
  /*) ;;
  *) DATA_ROOT="$REPO_ROOT/$DATA_ROOT" ;;
esac
if [ -n "${1:-}" ]; then
  SOURCE_DIR="$1"
elif [ -n "${YUKI_BACKUP_SOURCE:-}" ]; then
  SOURCE_DIR="$YUKI_BACKUP_SOURCE"
  case "$SOURCE_DIR" in
    /*) ;;
    *) SOURCE_DIR="$REPO_ROOT/$SOURCE_DIR" ;;
  esac
elif [ -n "${YUKI_WORKSPACE:-}" ]; then
  SOURCE_DIR="$YUKI_WORKSPACE"
  case "$SOURCE_DIR" in
    /*) ;;
    *) SOURCE_DIR="$DATA_ROOT/$SOURCE_DIR" ;;
  esac
else
  SOURCE_DIR="$DATA_ROOT/yuki_workspace"
fi
BACKUP_DIR="${2:-${YUKI_BACKUP_DIR:-$REPO_ROOT/BACKUP/YukiData}}"
RETENTION_DAYS="${YUKI_BACKUP_RETENTION_DAYS:-14}"
PYTHON_BIN="${YUKI_PYTHON:-}"

case "$RETENTION_DAYS" in
  ''|*[!0-9]*) echo "YUKI_BACKUP_RETENTION_DAYS must be a whole number." >&2; exit 2 ;;
esac
if [ "$RETENTION_DAYS" -lt 1 ] || [ "$RETENTION_DAYS" -gt 3650 ]; then
  echo "Retention must be between 1 and 3650 days." >&2
  exit 2
fi
if [ ! -d "$SOURCE_DIR" ]; then
  echo "Backup source does not exist: $SOURCE_DIR" >&2
  echo "Set YUKI_BACKUP_SOURCE/YUKI_WORKSPACE, create the configured workspace, or pass a source directory." >&2
  exit 1
fi
SOURCE_DIR="$(CDPATH= cd -- "$SOURCE_DIR" && pwd)"
mkdir -p "$BACKUP_DIR"
BACKUP_DIR="$(CDPATH= cd -- "$BACKUP_DIR" && pwd)"

case "$BACKUP_DIR/" in
  "$SOURCE_DIR/"*) echo "Backup destination must not be inside the source directory." >&2; exit 2 ;;
esac
case "$SOURCE_DIR/" in
  "$BACKUP_DIR/"*) echo "Source must not be inside the backup destination." >&2; exit 2 ;;
esac

if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3
  elif command -v python >/dev/null 2>&1; then PYTHON_BIN=python
  else echo "Python is required for SQLite-consistent snapshots." >&2; exit 1
  fi
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Configured Python executable was not found: $PYTHON_BIN" >&2
  exit 1
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
if [ -n "${YUKI_BACKUP_KEY_FILE:-}" ]; then
  ARCHIVE="$BACKUP_DIR/yuki-data-$STAMP.yukibak"
else
  ARCHIVE="$BACKUP_DIR/yuki-data-$STAMP.tar.gz"
fi
STAGING_ROOT="$(mktemp -d "$BACKUP_DIR/.yuki-stage-XXXXXX")"
SOURCE_NAME="$(basename -- "$SOURCE_DIR")"
SNAPSHOT_DIR="$STAGING_ROOT/$SOURCE_NAME"
TEMP_ARCHIVE="$STAGING_ROOT/$SOURCE_NAME.tar.gz"
cleanup() {
  rm -f "$TEMP_ARCHIVE"
  rm -rf "$STAGING_ROOT"
}
trap cleanup EXIT HUP INT TERM

"$PYTHON_BIN" "$SCRIPT_DIR/sqlite_workspace_snapshot.py" "$SOURCE_DIR" "$SNAPSHOT_DIR"
tar -czf "$TEMP_ARCHIVE" -C "$STAGING_ROOT" "$SOURCE_NAME"
# Validate archive before publishing it as a completed backup.
tar -tzf "$TEMP_ARCHIVE" >/dev/null
if [ -n "${YUKI_BACKUP_KEY_FILE:-}" ]; then
  "$PYTHON_BIN" "$SCRIPT_DIR/backup_crypto.py" encrypt "$TEMP_ARCHIVE" "$ARCHIVE" --key-file "$YUKI_BACKUP_KEY_FILE"
else
  mv "$TEMP_ARCHIVE" "$ARCHIVE"
fi
trap - EXIT HUP INT TERM
cleanup

find "$BACKUP_DIR" -type f \( -name 'yuki-data-*.tar.gz' -o -name 'yuki-data-*.yukibak' \) -mtime "+$RETENTION_DAYS" -exec rm -f {} \;

echo "Backup completed: $ARCHIVE"
if [ -n "${YUKI_BACKUP_KEY_FILE:-}" ]; then
  echo "Archive authenticated and encrypted with AES-256-GCM."
else
  echo "WARNING: archive is plaintext. Set YUKI_BACKUP_KEY_FILE to enable authenticated encryption."
fi
