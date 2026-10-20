"""Privacy-conscious structured logging for local Yuki operation.

Only explicitly approved metadata fields are serialized. Prompts, task goals,
tool arguments, provider output, credentials, and exception messages are never
included by this formatter.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_ALLOWED_FIELDS = frozenset(
    {
        "event",
        "component",
        "task_id",
        "status",
        "reason_code",
        "duration_ms",
        "step_count",
        "recovered_count",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per record with an explicit metadata allow-list."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name[:80],
        }
        for field in _ALLOWED_FIELDS:
            value = getattr(record, field, None)
            if value is None:
                continue
            if field in {"duration_ms", "step_count", "recovered_count"}:
                if type(value) is not int or value < 0:
                    continue
            elif not isinstance(value, str):
                continue
            else:
                value = value[:160]
            payload[field] = value
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def configure_logging(*, level: str = "INFO", stream=None) -> None:
    """Configure local JSON logs on stderr; no network or telemetry is initialized."""
    normalized = level.upper()
    if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("invalid log level")
    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(normalized)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    component: str,
    task_id: str | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    duration_ms: int | None = None,
    step_count: int | None = None,
    recovered_count: int | None = None,
) -> None:
    """Write an allow-listed lifecycle event without arbitrary caller metadata."""
    logger.log(
        level,
        event,
        extra={
            "event": event[:80],
            "component": component[:80],
            "task_id": task_id[:80] if isinstance(task_id, str) else None,
            "status": status[:80] if isinstance(status, str) else None,
            "reason_code": reason_code[:80] if isinstance(reason_code, str) else None,
            "duration_ms": duration_ms,
            "step_count": step_count,
            "recovered_count": recovered_count,
        },
    )
