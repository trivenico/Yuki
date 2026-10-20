"""Private local task-outcome memory; this does not train or modify an AI model.

History writes are atomic and permission-restricted on POSIX. The file is local
application state, not a multi-process database: concurrent writers should use a
transactional store instead of assuming read-modify-write is serializable.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.portable_paths import data_root

HISTORY_FILE = data_root() / "yuki_learning.json"
MAX_RECORDS = 500
MAX_TEXT = 1000
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
]
_FIELDS = ("task", "outcome", "approach", "lesson", "created_at")


def _clean(value: Any) -> str:
    if not isinstance(value, (str, int, float, bool)) and value is not None:
        value = "[unsupported value]"
    text = str(value or "")[:MAX_TEXT]
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def load_history() -> list[dict[str, str]]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    clean_records: list[dict[str, str]] = []
    for item in data[-MAX_RECORDS:]:
        if not isinstance(item, dict):
            continue
        clean_records.append({
            "task": _clean(item.get("task", "")),
            "outcome": _clean(item.get("outcome", "")),
            "approach": _clean(item.get("approach", "")),
            "lesson": _clean(item.get("lesson", "")),
            "created_at": _clean(item.get("created_at", ""))[:64],
        })
    return clean_records


def save_history(history: Any) -> bool:
    if not isinstance(history, list):
        return False
    safe_history: list[dict[str, str]] = []
    for item in history[-MAX_RECORDS:]:
        if not isinstance(item, dict):
            continue
        safe_history.append({
            key: (_clean(item.get(key, ""))[:64] if key == "created_at" else _clean(item.get(key, "")))
            for key in _FIELDS
        })
    parent = HISTORY_FILE.parent
    temp_name: str | None = None
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        fd, temp_name = tempfile.mkstemp(prefix=".yuki-learning-", suffix=".tmp", dir=parent)
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(safe_history, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, HISTORY_FILE)
        temp_name = None
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def record_outcome(task: str, outcome: str, approach: str = "", lesson: str = "") -> str:
    if not isinstance(task, str) or not task.strip():
        return "Please provide a task description."
    history = load_history()
    history.append({
        "task": _clean(task),
        "outcome": _clean(outcome),
        "approach": _clean(approach),
        "lesson": _clean(lesson),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    if not save_history(history):
        return "Could not save learning history."
    return "Outcome recorded. Lessons are stored locally; the AI model is not trained or modified."


def suggest_lessons(task: str) -> list[str]:
    terms = {word.lower() for word in re.findall(r"[a-zA-Z0-9]{4,}", str(task or ""))}
    if not terms:
        return []
    matches: list[str] = []
    for item in reversed(load_history()):
        lesson = item["lesson"]
        past = " ".join((item["task"], item["approach"])).lower()
        if lesson and any(term in past for term in terms) and lesson not in matches:
            matches.append(lesson)
        if len(matches) >= 5:
            break
    return matches


def clear_history() -> str:
    return "Learning history cleared." if save_history([]) else "Could not clear learning history."
