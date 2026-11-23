"""Privacy-safe local diagnostic event history for developer support.

Only event names, component names, timestamps, and exception class names are
persisted. Raw exception messages, prompts, credentials, paths, and task data
are intentionally excluded.
"""
from __future__ import annotations

import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from core.json_file_store import atomic_write_json, file_lock
from core.portable_paths import data_root

_MAX_EVENTS = 100
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")


class DiagnosticEventStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else data_root() / ".yuki" / "diagnostics.json"

    @staticmethod
    def _validate_name(value: str, field: str) -> str:
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            raise ValueError(f"{field} must be a simple bounded identifier.")
        return value

    def record(self, component: str, event: str, error: BaseException | None = None) -> dict:
        component = self._validate_name(component, "component")
        event = self._validate_name(event, "event")
        error_type = None
        if error is not None:
            error_type = self._validate_name(type(error).__name__, "error type")
        item = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "component": component,
            "event": event,
            "error_type": error_type,
        }
        with file_lock(self.path):
            existing = self._read_unlocked()
            existing.append(item)
            atomic_write_json(self.path, existing[-_MAX_EVENTS:])
        return dict(item)

    def recent(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= _MAX_EVENTS:
            raise ValueError("limit must be between 1 and 100.")
        if not self.path.exists():
            return []
        # Writers replace the JSON file atomically, so readers can safely
        # observe either complete version without creating a lock file.
        return [dict(item) for item in self._read_unlocked()[-limit:]]

    def _read_unlocked(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError("Diagnostic event history must be a regular single-link file.")
            if info.st_size > 128 * 1024:
                raise RuntimeError("Diagnostic event history exceeds its size limit.")
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Diagnostic event history is unreadable.") from exc
        if not isinstance(data, list) or len(data) > _MAX_EVENTS:
            raise RuntimeError("Diagnostic event history has an invalid format.")
        validated = []
        for item in data:
            if not isinstance(item, dict) or set(item) != {"timestamp", "component", "event", "error_type"}:
                raise RuntimeError("Diagnostic event history has an invalid record.")
            if not all(isinstance(item.get(key), str) for key in ("timestamp", "component", "event")):
                raise RuntimeError("Diagnostic event history has invalid metadata.")
            if len(item["timestamp"]) > 64:
                raise RuntimeError("Diagnostic event history timestamp is invalid.")
            try:
                datetime.fromisoformat(item["timestamp"])
            except ValueError as exc:
                raise RuntimeError("Diagnostic event history timestamp is invalid.") from exc
            self._validate_name(item["component"], "component")
            self._validate_name(item["event"], "event")
            if item["error_type"] is not None:
                self._validate_name(item["error_type"], "error type")
            validated.append(item)
        return validated


def record_diagnostic_event(component: str, event: str, error: BaseException | None = None) -> bool:
    """Best-effort instrumentation that must never interrupt application flow."""
    try:
        DiagnosticEventStore().record(component, event, error)
        return True
    except Exception:
        return False
