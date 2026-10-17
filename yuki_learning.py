"""Small local learning log based on task outcomes; not model self-training."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

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
        return data[-MAX_RECORDS:] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_history(history):
    temp = HISTORY_FILE.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(history[-MAX_RECORDS:], indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(HISTORY_FILE)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def record_outcome(task, outcome, approach="", lesson=""):
    if not str(task or "").strip():
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
    return "Outcome recorded. Yuki stores lessons locally; this does not train or modify its AI model."


def suggest_lessons(task):
    terms = {w.lower() for w in re.findall(r"[a-zA-Z0-9]{4,}", str(task or ""))}
    matches = []
    for item in reversed(load_history()):
        lesson = item.get("lesson", "")
        past = " ".join((item.get("task", ""), item.get("approach", ""))).lower()
        if lesson and terms and any(term in past for term in terms):
            if lesson not in matches:
                matches.append(lesson)
        if len(matches) >= 5:
            break
    return matches


def clear_history():
    return "Learning history cleared." if save_history([]) else "Could not clear learning history."
