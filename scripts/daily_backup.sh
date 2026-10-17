#!/usr/bin/env bash
# Create a timestamped local backup of Yuki's private data.
# Usage: sh scripts/daily_backup.sh [source_dir] [backup_dir]
set -eu
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="${1:-${YUKI_HOME:-$REPO_ROOT/YukiData}}"
BACKUP_DIR="${2:-${YUKI_BACKUP_DIR:-$REPO_ROOT/BACKUP/YukiData}}"
RETENTION_DAYS="${YUKI_BACKUP_RETENTION_DAYS:-14}"

case "$RETENTION_DAYS" in
  ''|*[!0-9]*) echo "YUKI_BACKUP_RETENTION_DAYS must be a whole number." >&2; exit 2 ;;
esac
if [ "$RETENTION_DAYS" -lt 1 ] || [ "$RETENTION_DAYS" -gt 3650 ]; then
  echo "Retention must be between 1 and 3650 days." >&2
  exit 2
fi
if [ ! -d "$SOURCE_DIR" ]; then
  echo "Backup source does not exist: $SOURCE_DIR" >&2
  echo "Create YukiData first or pass a source directory explicitly." >&2
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

STAMP="$(date '+%Y%m%d_%H%M%S')"
ARCHIVE="$BACKUP_DIR/yuki-data-$STAMP.tar.gz"
TEMP_ARCHIVE="$ARCHIVE.tmp"
cleanup() { rm -f "$TEMP_ARCHIVE"; }
trap cleanup EXIT HUP INT TERM

PARENT_DIR="$(dirname -- "$SOURCE_DIR")"
SOURCE_NAME="$(basename -- "$SOURCE_DIR")"
tar -czf "$TEMP_ARCHIVE" -C "$PARENT_DIR" "$SOURCE_NAME"
# Validate archive before publishing it as a completed backup.
tar -tzf "$TEMP_ARCHIVE" >/dev/null
mv "$TEMP_ARCHIVE" "$ARCHIVE"
trap - EXIT HUP INT TERM

find "$BACKUP_DIR" -type f -name 'yuki-data-*.tar.gz' -mtime "+$RETENTION_DAYS" -exec rm -f {} \;

echo "Backup completed: $ARCHIVE"
echo "Retention: $RETENTION_DAYS days. Keep the drive encrypted and store another copy separately."
