"""Small local security controls and metadata-only audit logging for Yuki."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from yuki_sqlite_store import SQLiteJsonStore

AUDIT_FILE = Path(__file__).with_name("yuki_audit.json")
AUDIT_DB_FILE = AUDIT_FILE.with_suffix(".sqlite3")
_AUDIT_KEY = "audit_events"
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


def _normalize_audit_events(data):
    if not isinstance(data, list):
        return []
    return [
        {"timestamp": str(item.get("timestamp", ""))[:64],
         "action": sanitize_text(item.get("action", ""), 80),
         "outcome": sanitize_text(item.get("outcome", ""), 32)}
        for item in data[-MAX_AUDIT_RECORDS:]
        if isinstance(item, dict)
    ]


def _audit_store():
    return SQLiteJsonStore(AUDIT_DB_FILE)


def load_audit_events():
    """Load transactional audit events, migrating valid legacy JSON once."""
    try:
        if AUDIT_DB_FILE.exists():
            stored = _audit_store().read_json(_AUDIT_KEY, None)
            if stored is not None:
                return _normalize_audit_events(stored)
        if not AUDIT_FILE.exists():
            return []
        try:
            legacy = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Preserve malformed legacy audit data for investigation.
            return []
        if not isinstance(legacy, list):
            return []
        safe = _normalize_audit_events(legacy)
        migrated = _audit_store().update_json(
            _AUDIT_KEY, lambda current: safe if current is None else current, default=None
        )
        return _normalize_audit_events(migrated)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return []


def write_audit_event(action, outcome):
    """Persist bounded metadata transactionally; never store prompts or arguments."""
    if not isinstance(action, str) or not isinstance(outcome, str):
        return False
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": sanitize_text(action, 80),
        "outcome": sanitize_text(outcome, 32),
    }
    try:
        # Migrate before appending if this is the first write and a legacy log exists.
        if not AUDIT_DB_FILE.exists() and AUDIT_FILE.exists():
            load_audit_events()
        _audit_store().update_json(
            _AUDIT_KEY,
            lambda current: (_normalize_audit_events(current) + [event])[-MAX_AUDIT_RECORDS:],
            default=[],
        )
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False
