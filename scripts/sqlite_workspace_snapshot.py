"""Create a point-in-time workspace copy with SQLite databases snapshotted safely.

This helper uses SQLite's online backup API so committed WAL content is included.
It copies non-database files as ordinary files; callers should still stop Yuki
when a whole-workspace application-level snapshot is required.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sqlite3
import sys


_SQLITE_HEADER = b"SQLite format 3\x00"
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class SnapshotError(RuntimeError):
    """Raised when a safe, complete workspace snapshot cannot be made."""


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sqlite_uri(path: Path) -> str:
    # as_uri percent-encodes special characters (including spaces and #).
    return path.resolve().as_uri() + "?mode=ro"


def _walk_workspace(source: Path):
    directories: list[Path] = []
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(dirnames):
            item = current_path / name
            if item.is_symlink():
                raise SnapshotError("workspace_contains_symlink")
            if not item.is_dir():
                raise SnapshotError("workspace_contains_non_directory")
            directories.append(item)
        dirnames.sort()
        for name in sorted(filenames):
            item = current_path / name
            if item.is_symlink() or not item.is_file():
                raise SnapshotError("workspace_contains_non_regular_file")
            files.append(item)
    return directories, files


def _is_sqlite_database(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError as exc:
        raise SnapshotError("workspace_file_unreadable") from exc


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    source_db = None
    target_db = None
    try:
        source_db = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=30.0)
        target_db = sqlite3.connect(str(destination), timeout=30.0)
        source_db.backup(target_db, pages=256, sleep=0.01)
        result = target_db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise SnapshotError("sqlite_snapshot_integrity_check_failed")
        target_db.commit()
        shutil.copystat(source, destination, follow_symlinks=False)
    except SnapshotError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise SnapshotError("sqlite_snapshot_failed") from exc
    finally:
        if target_db is not None:
            target_db.close()
        if source_db is not None:
            source_db.close()


def snapshot_workspace(source_dir: str | Path, destination_dir: str | Path) -> dict[str, int]:
    """Copy a workspace and replace live SQLite files with backup-API snapshots.

    Destination must not exist and neither path may contain the other. Symlinks
    and special files fail closed to prevent copying data outside the workspace.
    """
    source = Path(source_dir).expanduser().resolve(strict=True)
    destination = Path(destination_dir).expanduser().resolve(strict=False)
    if not source.is_dir():
        raise SnapshotError("workspace_source_must_be_directory")
    if destination.exists():
        raise SnapshotError("snapshot_destination_already_exists")
    if source == destination or _is_within(destination, source) or _is_within(source, destination):
        raise SnapshotError("snapshot_paths_must_be_disjoint")

    directories, files = _walk_workspace(source)
    databases = [path for path in files if _is_sqlite_database(path)]
    database_set = {path for path in databases}
    sidecars = {
        Path(str(database) + suffix)
        for database in databases
        for suffix in _SIDECAR_SUFFIXES
    }

    try:
        destination.mkdir(parents=True, exist_ok=False)
        for directory in directories:
            (destination / directory.relative_to(source)).mkdir(parents=True, exist_ok=True)
        for path in files:
            if path in database_set or path in sidecars:
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=False)
        for database in databases:
            target = destination / database.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            _snapshot_sqlite(database, target)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    return {"files_copied": len(files) - len(databases) - len(sidecars.intersection(files)),
            "sqlite_databases_snapshotted": len(databases)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Existing Yuki workspace directory")
    parser.add_argument("destination", help="New, empty snapshot directory")
    args = parser.parse_args(argv)
    try:
        result = snapshot_workspace(args.source, args.destination)
    except (OSError, SnapshotError) as exc:
        print(f"Workspace snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Workspace snapshot complete: "
        f"{result['sqlite_databases_snapshotted']} SQLite database(s), "
        f"{result['files_copied']} other file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
