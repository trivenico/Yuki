"""Local task-outcome memory; this does not train or modify an AI model."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from yuki_sqlite_store import SQLiteJsonStore

HISTORY_FILE = Path(__file__).with_name("yuki_learning.json")
HISTORY_DB_FILE = HISTORY_FILE.with_suffix(".sqlite3")
_HISTORY_KEY = "learning_history"
MAX_RECORDS = 500
MAX_TEXT = 1000
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
]


def _clean(value):
    text = str(value or "")[:MAX_TEXT]
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _normalize_history(value):
    if not isinstance(value, list):
        return []
    clean_records = []
    for item in value[-MAX_RECORDS:]:
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


def _store():
    return SQLiteJsonStore(HISTORY_DB_FILE)


def load_history():
    """Load SQLite history, migrating valid legacy JSON without deleting it."""
    try:
        if HISTORY_DB_FILE.exists():
            stored = _store().read_json(_HISTORY_KEY, None)
            if stored is not None:
                return _normalize_history(stored)

        if not HISTORY_FILE.exists():
            return []
        try:
            legacy = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Keep malformed legacy data intact for manual recovery.
            return []
        if not isinstance(legacy, list):
            return []
        safe = _normalize_history(legacy)
        migrated = _store().update_json(
            _HISTORY_KEY,
            lambda current: safe if current is None else current,
            default=None,
        )
        return _normalize_history(migrated)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return []


def save_history(history):
    """Persist normalized history atomically; return False on storage failure."""
    safe_history = _normalize_history(history)
    try:
        _store().write_json(_HISTORY_KEY, safe_history)
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False


def record_outcome(task, outcome, approach="", lesson=""):
    if not isinstance(task, str) or not task.strip():
        return "Please provide a task description."
    record = {
        "task": _clean(task),
        "outcome": _clean(outcome),
        "approach": _clean(approach),
        "lesson": _clean(lesson),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        store = _store()

        def append_record(current):
            history = _normalize_history(current)
            history.append(record)
            return history[-MAX_RECORDS:]

        # Atomic read-modify-write prevents concurrent outcomes from overwriting
        # each other. The legacy history is loaded first if this is a fresh DB.
        if not HISTORY_DB_FILE.exists() and HISTORY_FILE.exists():
            load_history()
        store.update_json(_HISTORY_KEY, append_record, default=[])
        return "Outcome recorded. Lessons are stored locally; the AI model is not trained or modified."
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return "Could not save learning history."


def suggest_lessons(task):
    terms = {word.lower() for word in re.findall(r"[a-zA-Z0-9]{4,}", str(task or ""))}
    if not terms:
        return []
    matches = []
    for item in reversed(load_history()):
        lesson = item["lesson"]
        past = " ".join((item["task"], item["approach"])).lower()
        if lesson and any(term in past for term in terms) and lesson not in matches:
            matches.append(lesson)
        if len(matches) >= 5:
            break
    return matches


def clear_history():
    return "Learning history cleared." if save_history([]) else "Could not clear learning history."
