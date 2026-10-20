"""Local, bounded token-usage accounting for provider responses.

Counts are exact only when the provider supplies usage metadata. Otherwise callers
may record an explicitly marked estimate; estimates must never be presented as exact.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading


@dataclass(frozen=True)
class UsageEvent:
    session_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated: bool
    created_at: str


class TokenUsageTracker:
    MAX_EVENTS = 10000

    def __init__(
        self, path=None, *, session_id="default", alert_threshold=100000, spike_multiplier=3.0
    ):
        self.path = Path(path).expanduser().resolve() if path else None
        self.session_id = str(session_id)[:100]
        if (
            isinstance(alert_threshold, bool)
            or not isinstance(alert_threshold, int)
            or alert_threshold < 1
        ):
            raise ValueError("alert_threshold must be a positive integer")
        if (
            isinstance(spike_multiplier, bool)
            or not isinstance(spike_multiplier, (int, float))
            or spike_multiplier <= 1
        ):
            raise ValueError("spike_multiplier must be greater than one")
        self.alert_threshold = alert_threshold
        self.spike_multiplier = float(spike_multiplier)
        self._lock = threading.RLock()
        self._events = self._load()

    def _load(self):
        if self.path is None or not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Usage log is unreadable; it was not overwritten.") from exc
        if not isinstance(raw, list) or len(raw) > self.MAX_EVENTS:
            raise RuntimeError("Usage log has invalid structure or exceeds event limit.")
        events = []
        for item in raw:
            if not isinstance(item, dict):
                raise RuntimeError("Usage log contains invalid event.")
            events.append(UsageEvent(**item))
        return events

    @staticmethod
    def _count(value, label):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10_000_000:
            raise ValueError(f"{label} must be an integer from 0 to 10000000")
        return value

    def record(self, *, provider, model="", input_tokens, output_tokens, estimated=False):
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 80:
            raise ValueError("provider must be a non-empty short string")
        if not isinstance(model, str) or len(model) > 120:
            raise ValueError("model must be a short string")
        if not isinstance(estimated, bool):
            raise ValueError("estimated must be boolean")
        with self._lock:
            if len(self._events) >= self.MAX_EVENTS:
                raise RuntimeError("Usage event limit reached; archive or clear the log.")
            event = UsageEvent(
                session_id=self.session_id,
                provider=provider.strip(),
                model=model,
                input_tokens=self._count(input_tokens, "input_tokens"),
                output_tokens=self._count(output_tokens, "output_tokens"),
                estimated=estimated,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self._events.append(event)
            self._save()
            return {"event": asdict(event), "summary": self.summary()}

    def summary(self):
        events = [e for e in self._events if e.session_id == self.session_id]
        total_input = sum(e.input_tokens for e in events)
        total_output = sum(e.output_tokens for e in events)
        previous = events[-2].input_tokens + events[-2].output_tokens if len(events) >= 2 else None
        current = events[-1].input_tokens + events[-1].output_tokens if events else 0
        alerts = []
        if total_input + total_output >= self.alert_threshold:
            alerts.append("session_token_threshold")
        if previous and current > previous * self.spike_multiplier:
            alerts.append("usage_spike")
        return {
            "session_id": self.session_id,
            "calls": len(events),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "exact_events": sum(not e.estimated for e in events),
            "estimated_events": sum(e.estimated for e in events),
            "alerts": alerts,
        }

    def _save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as stream:
                json.dump([asdict(e) for e in self._events], stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                tmp.unlink()
