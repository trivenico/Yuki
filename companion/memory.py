"""Small private local memory store with bounded entries and explicit deletion.

Memory is opt-in at the call site. No network, embeddings, or model training occur.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


class MemoryStore:
    MAX_ENTRIES = 2000
    MAX_TEXT = 4000

    def __init__(self, path: str | Path = "yuki_memory.json"):
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError("memory path must be a non-empty path.")
        requested = Path(path).expanduser()
        if requested.name in {"", ".", ".."}:
            raise ValueError("memory path is invalid.")
        self.path = requested.parent.resolve() / requested.name
        self._lock = RLock()
        self._prepare_private_file()

    def _prepare_private_file(self) -> None:
        """Create or validate a private regular file without following symlinks."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            if os.name == "posix":
                flags |= getattr(os, "O_NOFOLLOW", 0)
            elif self.path.is_symlink():
                raise RuntimeError("Memory path must not be a symbolic link.")
            descriptor = os.open(self.path, flags, 0o600)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeError("Memory path must be a regular file.")
                if metadata.st_nlink != 1:
                    raise RuntimeError("Memory file hard links are not allowed.")
                if os.name == "posix":
                    os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError("Memory file cannot be secured.") from exc

    def _load(self) -> list[dict]:
        self._prepare_private_file()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if os.name == "posix":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Memory file is unreadable; no data was overwritten.") from exc
        if not isinstance(raw, list) or len(raw) > self.MAX_ENTRIES:
            raise RuntimeError("Memory file has an invalid structure or exceeds limits.")
        for item in raw:
            if (not isinstance(item, dict)
                    or not all(k in item for k in ("id", "created_at", "text"))
                    or not isinstance(item["id"], str)
                    or not isinstance(item["created_at"], str)
                    or not isinstance(item["text"], str)
                    or len(item["text"]) > self.MAX_TEXT):
                raise RuntimeError("Memory file contains an invalid entry.")
        return raw

    def _save(self, entries: list[dict]) -> None:
        self._prepare_private_file()
        temp_name = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=".yuki-memory-", suffix=".tmp", dir=str(self.path.parent)
            )
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(entries[-self.MAX_ENTRIES:], handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            temp_name = None
            if os.name == "posix":
                directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            raise RuntimeError("Memory file could not be saved safely.") from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    def add(self, text: str, *, category: str = "note") -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT:
            raise ValueError(f"Memory text must be 1-{self.MAX_TEXT} characters.")
        if category not in {"note", "preference", "lesson"}:
            raise ValueError("category must be note, preference, or lesson.")
        with self._lock:
            entries = self._load()
            item = {"id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
                    "category": category, "text": text.strip()}
            entries.append(item)
            self._save(entries)
            return dict(item)

    def list(self, limit: int = 50) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        with self._lock:
            return [dict(item) for item in self._load()[-limit:]]

    def delete(self, memory_id: str) -> bool:
        if not isinstance(memory_id, str) or not memory_id.strip():
            return False
        with self._lock:
            entries = self._load()
            remaining = [item for item in entries if item["id"] != memory_id]
            if len(remaining) == len(entries):
                return False
            self._save(remaining)
            return True

    def clear(self) -> int:
        """Explicit destructive operation; callers must obtain user approval first."""
        with self._lock:
            entries = self._load()
            self._save([])
            return len(entries)
