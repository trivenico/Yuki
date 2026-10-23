"""Read-only legacy memory/learning bridge with dry-run-first import.

The bridge snapshots legacy note and learning-history JSON into the unified
MemoryStore-compatible JSON shape. Source files are never edited. Applying
requires an explicit flag, creates exclusive backups, and refuses overwrite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4


MAX_MEMORY_RECORDS = 2000
MAX_LEARNING_RECORDS = 500
MAX_TEXT = 4000
MAX_LEARNING_FIELD = 1000
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
)


class LegacyMemoryBridgeError(RuntimeError):
    """Raised when legacy data cannot be safely imported."""


def _redact(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _load_json_list(path: Path, *, maximum: int, missing_ok: bool) -> list[Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if missing_ok:
            return []
        raise LegacyMemoryBridgeError(f"Required source file is missing: {path.name}") from None
    except OSError as exc:
        raise LegacyMemoryBridgeError(f"Cannot read source file {path.name}; no changes made.") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LegacyMemoryBridgeError(f"Source file {path.name} is malformed; no changes made.") from exc
    if not isinstance(value, list) or len(value) > maximum:
        raise LegacyMemoryBridgeError(f"Source file {path.name} has an invalid structure or exceeds limits.")
    return value


def _memory_entries(path: Path) -> list[dict[str, Any]]:
    raw = _load_json_list(path, maximum=MAX_MEMORY_RECORDS, missing_ok=True)
    output = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            text, category, created_at, source_id = item.strip(), "note", "", f"legacy-memory-{index}"
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            text = item["text"].strip()
            category = item.get("category", "note")
            if not isinstance(category, str) or category not in {"note", "preference", "lesson"}:
                raise LegacyMemoryBridgeError(f"Memory record {index} has an invalid category.")
            created_at = item.get("created_at", "")
            source_id = item.get("id", f"legacy-memory-{index}")
            if not isinstance(created_at, str) or len(created_at) > 64:
                raise LegacyMemoryBridgeError(f"Memory record {index} has an invalid timestamp.")
            if not isinstance(source_id, (str, int)) or len(str(source_id)) > 200:
                raise LegacyMemoryBridgeError(f"Memory record {index} has an invalid ID.")
        else:
            raise LegacyMemoryBridgeError(f"Memory record {index} is unsupported; no changes made.")
        if not text:
            continue
        if len(text) > MAX_TEXT:
            raise LegacyMemoryBridgeError(f"Memory record {index} exceeds the text limit.")
        output.append({
            "id": uuid4().hex,
            "created_at": created_at,
            "category": category,
            "text": _redact(text),
            "source": "legacy_memory",
            "source_id": str(source_id),
        })
    return output


def _learning_entries(path: Path) -> list[dict[str, Any]]:
    raw = _load_json_list(path, maximum=MAX_LEARNING_RECORDS, missing_ok=True)
    output = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise LegacyMemoryBridgeError(f"Learning record {index} is not an object; no changes made.")
        fields = {}
        for field in ("task", "outcome", "approach", "lesson"):
            value = item.get(field, "")
            if not isinstance(value, str):
                raise LegacyMemoryBridgeError(f"Learning record {index} has a non-text {field} field.")
            if len(value) > MAX_LEARNING_FIELD:
                raise LegacyMemoryBridgeError(f"Learning record {index} field {field} exceeds the text limit.")
            fields[field] = _redact(value.strip())
        created_at = item.get("created_at", "")
        if not isinstance(created_at, str) or len(created_at) > 64:
            raise LegacyMemoryBridgeError(f"Learning record {index} has an invalid timestamp.")
        text_parts = [
            f"{label.title()}: {fields[label]}"
            for label in ("task", "outcome", "approach", "lesson")
            if fields[label]
        ]
        if not text_parts:
            continue
        source_id = hashlib.sha256(
            json.dumps([created_at, fields], sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:32]
        output.append({
            "id": uuid4().hex,
            "created_at": created_at,
            "category": "lesson",
            "text": "\n".join(text_parts)[:MAX_TEXT],
            "source": "legacy_learning",
            "source_id": source_id,
            **fields,
        })
    return output


def _exclusive_backup(source: Path) -> str:
    backup = source.with_name(source.name + ".pre-phase104.bak")
    try:
        fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise LegacyMemoryBridgeError(f"Backup already exists for {source.name}; refusing to overwrite it.") from exc
    try:
        with os.fdopen(fd, "wb") as target, source.open("rb") as original:
            shutil.copyfileobj(original, target)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        try:
            backup.unlink()
        except OSError:
            pass
        raise
    return str(backup)


def bridge_legacy_memory(
    memory_path: str | Path,
    learning_path: str | Path,
    destination_path: str | Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview by default; apply a combined, non-destructive snapshot explicitly."""
    memory_src = Path(memory_path).expanduser().resolve()
    learning_src = Path(learning_path).expanduser().resolve()
    destination = Path(destination_path).expanduser().resolve()
    sources = (memory_src, learning_src)
    if destination in sources:
        raise LegacyMemoryBridgeError("Destination must differ from both source files.")
    if memory_src == learning_src:
        raise LegacyMemoryBridgeError("Memory and learning sources must be different files.")
    if destination.exists():
        raise LegacyMemoryBridgeError("Destination already exists; refusing to overwrite it.")
    entries = _memory_entries(memory_src) + _learning_entries(learning_src)
    result: dict[str, Any] = {
        "applied": False,
        "note_count": len(entries),
        "memory_count": sum(e["source"] == "legacy_memory" for e in entries),
        "learning_count": sum(e["source"] == "legacy_learning" for e in entries),
        "destination": str(destination),
        "backups": [],
    }
    if not apply:
        return result

    destination.parent.mkdir(parents=True, exist_ok=True)
    backups = []
    try:
        for source in sources:
            if source.is_file():
                backups.append(_exclusive_backup(source))
        fd, temp_name = tempfile.mkstemp(
            prefix=".yuki-phase104-", suffix=".tmp", dir=str(destination.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(entries, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temp_name, destination)
        except FileExistsError as exc:
            raise LegacyMemoryBridgeError("Destination appeared during import; refusing to overwrite it.") from exc
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
    except BaseException:
        # Backups intentionally remain if a later step fails; they are recovery copies.
        raise
    result.update({"applied": True, "backups": backups})
    return result
