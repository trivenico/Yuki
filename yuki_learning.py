"""Local task-outcome memory; this does not train or modify an AI model."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.json_file_store import atomic_write_json, file_lock

HISTORY_FILE = Path(__file__).with_name("yuki_learning.json")
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


def _load_history_unlocked():
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Learning history is unreadable; no data was overwritten.") from exc
    if not isinstance(data, list):
        raise RuntimeError("Learning history has an invalid structure.")
    clean_records = []
    required = {"task", "outcome", "approach", "lesson", "created_at"}
    for item in data:
        if not isinstance(item, dict) or not required.issubset(item):
            raise RuntimeError("Learning history contains a malformed record; no data was overwritten.")
        if any(not isinstance(item[key], str) for key in required):
            raise RuntimeError("Learning history contains invalid field types; no data was overwritten.")
        clean_records.append({
            "task": _clean(item["task"]),
            "outcome": _clean(item["outcome"]),
            "approach": _clean(item["approach"]),
            "lesson": _clean(item["lesson"]),
            "created_at": _clean(item["created_at"])[:64],
        })
    return clean_records[-MAX_RECORDS:]


def _save_history_unlocked(history):
    if not isinstance(history, list):
        raise ValueError("Learning history must be a list.")
    required = {"task", "outcome", "approach", "lesson", "created_at"}
    safe_history = []
    for item in history:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("Learning history contains a malformed record.")
        if any(not isinstance(item[key], str) for key in required):
            raise ValueError("Learning history contains invalid field types.")
        safe_history.append({
            "task": _clean(item["task"]),
            "outcome": _clean(item["outcome"]),
            "approach": _clean(item["approach"]),
            "lesson": _clean(item["lesson"]),
            "created_at": _clean(item["created_at"])[:64],
        })
    safe_history = safe_history[-MAX_RECORDS:]
    atomic_write_json(HISTORY_FILE, safe_history)
    return True


def load_history():
    with file_lock(HISTORY_FILE):
        return _load_history_unlocked()


def save_history(history):
    with file_lock(HISTORY_FILE):
        try:
            return _save_history_unlocked(history)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False


def record_outcome(task, outcome, approach="", lesson=""):
    if not isinstance(task, str) or not task.strip():
        return "Please provide a task description."
    with file_lock(HISTORY_FILE):
        try:
            history = _load_history_unlocked()
            history.append({
                "task": _clean(task),
                "outcome": _clean(outcome),
                "approach": _clean(approach),
                "lesson": _clean(lesson),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            _save_history_unlocked(history)
        except (OSError, RuntimeError, TypeError, ValueError):
            return "Could not save learning history."
    return "Outcome recorded. Lessons are stored locally; the AI model is not trained or modified."


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
