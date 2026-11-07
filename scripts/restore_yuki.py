"""Restore a verified Yuki SQLite backup into a new workspace directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile


SUPPORTED_FORMAT = "yuki-sqlite-backup-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Backup manifest contains an invalid relative path.")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError("Backup manifest contains an unsafe relative path.")
    return Path(*parsed.parts)


def _verify_database(path: Path) -> None:
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10) as db:
            result = db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"Backup contains an unreadable SQLite database: {path.name}") from exc
    if not result or result[0] != "ok":
        raise ValueError(f"SQLite integrity check failed: {path.name}")


def restore_backup(backup: Path, destination: Path) -> dict:
    backup = backup.expanduser().resolve()
    destination = destination.expanduser().resolve()
    manifest_path = backup / "manifest.json"
    if not backup.is_dir() or not manifest_path.is_file():
        raise ValueError("Backup directory or manifest is missing.")
    if destination.exists():
        raise FileExistsError("Restore destination already exists; refusing to overwrite it.")
    if destination == backup or backup in destination.parents or destination in backup.parents:
        raise ValueError("Backup and restore destination must be separate directories.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Backup manifest is unreadable or invalid JSON.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != SUPPORTED_FORMAT:
        raise ValueError("Unsupported backup manifest format.")
    entries = manifest.get("databases")
    if (not isinstance(entries, list) or not entries
            or manifest.get("database_count") != len(entries)):
        raise ValueError("Backup manifest database list is invalid.")

    validated = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Backup manifest contains an invalid database entry.")
        relative = _safe_relative_path(entry.get("path"))
        backup_relative = _safe_relative_path(entry.get("backup_path"))
        if relative in seen:
            raise ValueError("Backup manifest contains duplicate database paths.")
        seen.add(relative)
        source = (backup / backup_relative).resolve()
        if backup not in source.parents or not source.is_file():
            raise ValueError("Backup database path escapes the backup directory or is missing.")
        size = entry.get("size_bytes")
        digest = entry.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Backup manifest contains an invalid database size.")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Backup manifest contains an invalid SHA-256 checksum.")
        if source.stat().st_size != size or _sha256(source) != digest:
            raise ValueError(f"Backup checksum or size verification failed: {relative.as_posix()}")
        _verify_database(source)
        validated.append((relative, source))

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    try:
        for relative, source in validated:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            _verify_database(target)
        # Re-check destination immediately before publication; never replace existing data.
        if destination.exists():
            raise FileExistsError("Restore destination appeared during restore; refusing to overwrite it.")
        os.rename(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"destination": str(destination), "database_count": len(validated), "verified": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, help="Backup directory containing manifest.json")
    parser.add_argument("--destination", required=True, help="New workspace directory; must not exist")
    args = parser.parse_args()
    try:
        report = restore_backup(Path(args.backup), Path(args.destination))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Restore failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"Restore verified: {report['database_count']} database(s) at {report['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
