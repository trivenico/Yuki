"""Small local security controls and metadata-only audit logging for Yuki."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

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
    text = str(value or "")[: max(0, limit)]
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


def load_audit_events():
    try:
        data = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        {
            "timestamp": str(item.get("timestamp", ""))[:64],
            "action": str(item.get("action", ""))[:80],
            "outcome": str(item.get("outcome", ""))[:32],
        }
        for item in data[-MAX_AUDIT_RECORDS:]
        if isinstance(item, dict)
    ]


def write_audit_event(action, outcome):
    """Store only bounded metadata. Do not pass prompts, arguments, or secrets."""
    if not isinstance(action, str) or not isinstance(outcome, str):
        return False
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": sanitize_text(action, 80),
        "outcome": sanitize_text(outcome, 32),
    }
    data = load_audit_events()
    data.append(event)
    temp = AUDIT_FILE.with_suffix(".tmp")
    try:
        temp.write_text(
            json.dumps(data[-MAX_AUDIT_RECORDS:], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temp.replace(AUDIT_FILE)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
