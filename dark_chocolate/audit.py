"""Append-only JSONL audit events with basic secret redaction."""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
_REDACT = re.compile(r"(?i)(api[_-]?key|token|password|authorization)(\s*[:=]\s*)([^\s,;]+)")
_LOCK = Lock()
def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, str):
        return _REDACT.sub(r"\1\2[REDACTED]", value)[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return repr(value)[:1000]
class AuditLogger:
    def __init__(self, path: str | Path = "data/logs/audit.jsonl") -> None:
        self.path = Path(path)
    def log(self, event: str, **fields: Any) -> None:
        row = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **_safe(fields)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
