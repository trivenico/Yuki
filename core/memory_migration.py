"""Explicit, non-destructive migration from legacy JSON memory to unified notes.

The migration is dry-run by default, never edits the source, refuses to overwrite
an existing destination, and creates a source backup before writing. It is not
an automatic startup hook and does not synchronize data to any remote system.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from companion.memory import MemoryStore


class MemoryMigrationError(RuntimeError):
    """Raised when a source cannot be safely migrated."""


def _read_legacy(path: Path) -> list[tuple[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryMigrationError("Source is unreadable; nothing was changed.") from exc
    if not isinstance(data, list) or len(data) > MemoryStore.MAX_ENTRIES:
        raise MemoryMigrationError("Source must be a bounded JSON list.")
    notes: list[tuple[str, str]] = []
    for item in data:
        if isinstance(item, str):
            text, category = item.strip(), "note"
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            text = item["text"].strip()
            category = item.get("category", "note")
            if not isinstance(category, str) or category not in {"note", "preference", "lesson"}:
                category = "note"
        else:
            raise MemoryMigrationError("Source contains an unsupported record; nothing was changed.")
        if not text:
            continue
        if len(text) > MemoryStore.MAX_TEXT:
            raise MemoryMigrationError("A note exceeds the supported text length; nothing was changed.")
        notes.append((text, category))
    return notes


def migrate_memory(source: str | Path, destination: str | Path, *, apply: bool = False) -> dict[str, Any]:
    """Preview or apply a one-time migration; preview is the default."""
    src = Path(source).expanduser().resolve()
    dst = Path(destination).expanduser().resolve()
    if src == dst:
        raise MemoryMigrationError("Source and destination must be different files.")
    if not src.is_file():
        raise MemoryMigrationError("Source file does not exist.")
    if dst.exists():
        raise MemoryMigrationError("Destination already exists; refusing to overwrite it.")
    notes = _read_legacy(src)
    result: dict[str, Any] = {
        "applied": False,
        "source": str(src),
        "destination": str(dst),
        "note_count": len(notes),
        "backup": None,
    }
    if not apply:
        return result

    dst.parent.mkdir(parents=True, exist_ok=True)
    backup = src.with_name(src.name + ".pre-phase102.bak")
    if backup.exists():
        raise MemoryMigrationError("Migration backup already exists; refusing to overwrite it.")
    shutil.copy2(src, backup)
    fd, temp_name = tempfile.mkstemp(prefix=".yuki-migration-", suffix=".tmp", dir=str(dst.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            entries = [
                {
                    "id": uuid4().hex,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "category": category,
                    "text": text,
                }
                for text, category in notes
            ]
            json.dump(entries, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic same-filesystem link publishes the complete staged file without
        # overwriting a destination created concurrently.
        os.link(temp_name, dst)
    except FileExistsError as exc:
        raise MemoryMigrationError("Destination appeared during migration; refusing to overwrite it.") from exc
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    result.update({"applied": True, "backup": str(backup)})
    return result
