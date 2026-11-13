"""Explicit, verified migration of a closed Yuki desktop data directory.

Migration never deletes or modifies the source. Callers must obtain explicit
user consent before invoking migrate_data(); previews are read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


class MigrationError(RuntimeError):
    """Raised when migration cannot safely complete."""


MAX_MIGRATION_FILES = 20_000
MAX_MIGRATION_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class MigrationPreview:
    source: Path
    destination: Path
    files: tuple[str, ...]
    total_bytes: int


def _inventory(source: Path) -> list[Path]:
    root = source.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise MigrationError("The selected migration source must be a directory.")
    found: list[Path] = []
    total_bytes = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            if (current_path / name).is_symlink():
                raise MigrationError("Migration source contains a symbolic link.")
        for name in names:
            item = current_path / name
            if item.is_symlink() or not item.is_file():
                raise MigrationError("Migration source contains an unsupported file.")
            try:
                total_bytes += item.stat().st_size
            except OSError as exc:
                raise MigrationError("Migration source changed during inventory.") from exc
            if len(found) >= MAX_MIGRATION_FILES:
                raise MigrationError(
                    f"Migration exceeds the {MAX_MIGRATION_FILES:,}-file safety limit."
                )
            if total_bytes > MAX_MIGRATION_BYTES:
                raise MigrationError(
                    "Migration exceeds the 2 GiB initial safety limit."
                )
            found.append(item)
    if any(
        item.relative_to(root).as_posix().casefold() == "migration-manifest.json"
        for item in found
    ):
        raise MigrationError(
            "Source contains the reserved migration-manifest.json filename; rename it before migration."
        )
    databases = {
        item for item in found if item.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
    }
    sidecars = {
        Path(str(database) + suffix)
        for database in databases
        for suffix in ("-wal", "-shm", "-journal")
    }
    found = [item for item in found if item not in sidecars]
    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def preview_migration(source: Path, destination: Path) -> MigrationPreview:
    source_path = source.expanduser()
    destination_path = destination.expanduser()
    if source_path.is_symlink():
        raise MigrationError("Migration source must not be a symbolic link.")
    if destination_path.is_symlink():
        raise MigrationError("Migration destination must not be a symbolic link.")
    root = source_path.resolve(strict=True)
    target = destination_path.resolve()
    if root == target or root in target.parents or target in root.parents:
        raise MigrationError("Source and destination must be separate directories.")
    if target.exists() and any(target.iterdir()):
        raise MigrationError("Destination is not empty; migration will not overwrite data.")
    files = _inventory(root)
    return MigrationPreview(
        source=root,
        destination=target,
        files=tuple(item.relative_to(root).as_posix() for item in files),
        total_bytes=sum(item.stat().st_size for item in files),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_sqlite(source: Path, destination: Path) -> None:
    # Path.as_uri() correctly escapes spaces, '#', '?', and other URI characters.
    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise MigrationError(f"SQLite integrity check failed for {source.name}.")
        dst.commit()
    finally:
        dst.close()
        src.close()


def migrate_data(source: Path, destination: Path) -> dict:
    """Copy to a staging directory, verify, then atomically publish destination.

    The source is retained. SQLite databases are copied using SQLite's online
    backup API so a valid snapshot is produced; all other files are byte-copied.
    """
    preview = preview_migration(source, destination)
    target = preview.destination
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".yuki-migrate-", dir=target.parent))
    try:
        for relative in preview.files:
            src = preview.source / relative
            dst = stage / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                _copy_sqlite(src, dst)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
            if _sha256(src) != _sha256(dst) and src.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
                raise MigrationError(f"Verification failed for {relative}.")
        manifest = {
            "schema_version": 1,
            "source_kind": "user-selected-local-directory",
            "files": [
                {"path": rel, "size": (stage / rel).stat().st_size, "sha256": _sha256(stage / rel)}
                for rel in preview.files
            ],
        }
        (stage / "migration-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        # Re-check target immediately before publication; never merge/overwrite.
        if target.exists():
            if any(target.iterdir()):
                raise MigrationError("Destination changed during migration; no data was replaced.")
            target.rmdir()
        stage.rename(target)
        return manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
