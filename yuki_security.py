"""Small local security controls and metadata-only audit logging for Yuki."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.json_file_store import atomic_write_json, file_lock

AUDIT_FILE = Path(__file__).with_name("yuki_audit.json")
MAX_AUDIT_RECORDS = 1000
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
]


def sanitize_text(value, limit=1000):
    """Bound text and redact common token/credential patterns before local storage."""
    text = str(value or "")[:max(0, limit)]
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class SecurityPolicy:
    """Default-deny policy for tool names; never permits arbitrary shell execution."""

    def __init__(self, allowed_tools=None):
        defaults = {"time", "list_files", "save_note", "calculate"}
        self._allowed_tools = set(defaults if allowed_tools is None else allowed_tools)

    def authorize_tool(self, name):
        return isinstance(name, str) and name in self._allowed_tools

    def allowed_tools(self):
        return sorted(self._allowed_tools)


def _load_audit_events_unlocked():
    try:
        data = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Audit log is unreadable; no data was overwritten.") from exc
    if not isinstance(data, list):
        raise RuntimeError("Audit log has an invalid structure; no data was overwritten.")
    events = []
    required = {"timestamp", "action", "outcome"}
    for item in data:
        if not isinstance(item, dict) or not required.issubset(item):
            raise RuntimeError("Audit log contains a malformed event; no data was overwritten.")
        if any(not isinstance(item[key], str) for key in required):
            raise RuntimeError("Audit log contains invalid field types; no data was overwritten.")
        events.append({
            "timestamp": item["timestamp"][:64],
            "action": item["action"][:80],
            "outcome": item["outcome"][:32],
        })
    return events[-MAX_AUDIT_RECORDS:]


def load_audit_events():
    with file_lock(AUDIT_FILE):
        return _load_audit_events_unlocked()


def write_audit_event(action, outcome):
    """Store only bounded metadata. Do not pass prompts, arguments, or secrets."""
    if not isinstance(action, str) or not isinstance(outcome, str):
        return False
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": sanitize_text(action, 80),
        "outcome": sanitize_text(outcome, 32),
    }
    try:
        with file_lock(AUDIT_FILE):
            data = _load_audit_events_unlocked()
            data.append(event)
            atomic_write_json(AUDIT_FILE, data[-MAX_AUDIT_RECORDS:])
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
