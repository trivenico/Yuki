"""Local task-outcome memory; this does not train or modify an AI model."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from yuki_storage import atomic_write_text

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


def load_history():
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    clean_records = []
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


def save_history(history):
    safe_history = []
    for item in history[-MAX_RECORDS:]:
        if not isinstance(item, dict):
            continue
        safe_history.append({
            "task": _clean(item.get("task", "")),
            "outcome": _clean(item.get("outcome", "")),
            "approach": _clean(item.get("approach", "")),
            "lesson": _clean(item.get("lesson", "")),
            "created_at": _clean(item.get("created_at", ""))[:64],
        })
    return atomic_write_text(
        HISTORY_FILE,
        json.dumps(safe_history, indent=2, ensure_ascii=False),
    )


def record_outcome(task, outcome, approach="", lesson=""):
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
