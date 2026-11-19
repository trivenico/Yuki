"""Explicit inventory of SQLite databases owned by the production web composition.

This module intentionally avoids workspace globbing. A database must be named by
the application composition or registered explicitly by its owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import stat
from typing import Mapping


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class BackupInventoryError(RuntimeError):
    """The configured SQLite backup inventory is unsafe or incomplete."""


@dataclass(frozen=True)
class SQLiteDatabaseEntry:
    name: str
    path: Path


def build_web_sqlite_inventory(
    config: dict,
    *,
    additional_databases: Mapping[str, str | Path] | None = None,
) -> tuple[SQLiteDatabaseEntry, ...]:
    """Return the exact SQLite files created by scripts/run_web.py.

    Optional databases owned by separately composed services must be supplied
    explicitly. PostgreSQL conversation storage is not represented as SQLite.
    """
    if not isinstance(config, dict):
        raise TypeError("config_must_be_dict")
    workspace_value = config.get("workspace")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise BackupInventoryError("workspace_not_configured")
    workspace = Path(workspace_value).expanduser().resolve(strict=True)
    if not workspace.is_dir() or workspace.is_symlink():
        raise BackupInventoryError("workspace_must_be_existing_directory")

    backend = config.get("storage_backend", "sqlite")
    if backend not in {"sqlite", "postgres"}:
        raise BackupInventoryError("unsupported_storage_backend")

    candidates: dict[str, Path] = {
        "authentication": workspace / "auth.sqlite3",
        "inference_and_device_pairing": workspace / "inference.sqlite3",
        "income_opportunities": workspace / "income-opportunities.sqlite3",
    }
    if backend == "sqlite":
        candidates["conversations"] = workspace / "conversations.sqlite3"

    if additional_databases is not None:
        if not isinstance(additional_databases, Mapping):
            raise BackupInventoryError("additional_databases_must_be_mapping")
        for name, raw_path in additional_databases.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise BackupInventoryError("invalid_additional_database_name")
            if name in candidates:
                raise BackupInventoryError("duplicate_database_name")
            if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
                raise BackupInventoryError("invalid_additional_database_path")
            candidates[name] = Path(raw_path).expanduser()

    entries: list[SQLiteDatabaseEntry] = []
    seen_paths: set[Path] = set()
    for name, candidate in sorted(candidates.items()):
        if candidate.is_symlink():
            raise BackupInventoryError("database_symlink_not_allowed")
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except OSError as exc:
            raise BackupInventoryError("database_file_missing") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BackupInventoryError("database_must_be_single_link_regular_file")
        if resolved in seen_paths:
            raise BackupInventoryError("duplicate_database_path")
        seen_paths.add(resolved)
        entries.append(SQLiteDatabaseEntry(name=name, path=resolved))
    return tuple(entries)
