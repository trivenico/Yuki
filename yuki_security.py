"""Small local security controls and metadata-only audit logging for Yuki."""

import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT_FILE = Path(__file__).with_name("yuki_audit.json")
MAX_AUDIT_RECORDS = 1000


class SecurityPolicy:
    """Default-deny policy for tool names; never permits arbitrary shell execution."""

    def __init__(self, allowed_tools=None):
        self._allowed_tools = set(allowed_tools or {"time", "list_files", "save_note", "calculate"})

    def authorize_tool(self, name):
        return isinstance(name, str) and name in self._allowed_tools

    def allowed_tools(self):
        return sorted(self._allowed_tools)


def write_audit_event(action, outcome):
    """Store only bounded metadata. Do not pass prompts, arguments, or secrets."""
    if not isinstance(action, str) or not isinstance(outcome, str):
        return False
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action[:80],
        "outcome": outcome[:32],
    }
    try:
        data = json.loads(AUDIT_FILE.read_text(encoding="utf-8")) if AUDIT_FILE.exists() else []
        if not isinstance(data, list):
            data = []
    except (OSError, json.JSONDecodeError):
        data = []
    data = [item for item in data if isinstance(item, dict)][-(MAX_AUDIT_RECORDS - 1):]
    data.append(event)
    temp = AUDIT_FILE.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(AUDIT_FILE)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
