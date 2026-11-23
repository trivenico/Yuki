"""Bounded recurring operational-signal detection.

Signals are grouped only by allowlisted event kind and UTC time bucket.
No task payload, result, definition, exception text, or identity is inspected.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Any, Iterable

_SIGNAL_KINDS = frozenset({"task.failed", "task.blocked", "task.interrupted"})
_MAX_EVENTS = 10_000


@dataclass(frozen=True)
class RecurringSignal:
    kind: str
    current_count: int
    previous_count: int
    threshold: int
    trend: str


@dataclass(frozen=True)
class RecurringSignalReport:
    generated_at: str
    window_days: int
    signals: tuple[RecurringSignal, ...]

    def render(self) -> str:
        lines = [
            "Yuki Recurring Operational Signals",
            f"Generated: {self.generated_at}",
            f"Window: {self.window_days} days (current vs preceding equal-length window)",
        ]
        if not self.signals:
            lines.append("No recurring signal met the configured threshold.")
        for signal in self.signals:
            lines.append(
                f"- {signal.kind}: current={signal.current_count}, "
                f"previous={signal.previous_count}, trend={signal.trend}"
            )
        lines.append("Signals indicate repeated event categories, not proven root causes.")
        lines.append("No task, alert, worker, or permission state was changed.")
        return "\n".join(lines)


def detect_recurring_signals(
    *, events: Iterable[Any], window_days: int = 7, threshold: int = 3,
    now: datetime | None = None,
) -> RecurringSignalReport:
    """Compare two adjacent UTC windows using event kind and timestamp only."""
    if type(window_days) is not int or not 1 <= window_days <= 30:
        raise ValueError("window_days must be an integer from 1 to 30.")
    if type(threshold) is not int or not 2 <= threshold <= 1000:
        raise ValueError("threshold must be an integer from 2 to 1000.")
    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime.")
    current = current.astimezone(timezone.utc)
    rows = list(islice(events, _MAX_EVENTS + 1))
    if len(rows) > _MAX_EVENTS:
        raise ValueError("events exceeds the 10000-record analysis limit.")

    current_start = current - timedelta(days=window_days)
    previous_start = current - timedelta(days=2 * window_days)
    current_counts: Counter[str] = Counter()
    previous_counts: Counter[str] = Counter()

    for event in rows:
        kind = getattr(event, "kind", None)
        if kind not in _SIGNAL_KINDS:
            continue
        try:
            timestamp = datetime.fromisoformat(getattr(event, "created_at", None))
            if timestamp.tzinfo is None:
                continue
            timestamp = timestamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if current_start < timestamp <= current:
            current_counts[kind] += 1
        elif previous_start < timestamp <= current_start:
            previous_counts[kind] += 1

    signals = []
    for kind in sorted(_SIGNAL_KINDS):
        count = current_counts[kind]
        if count < threshold:
            continue
        previous = previous_counts[kind]
        trend = "increasing" if count > previous else "decreasing" if count < previous else "unchanged"
        signals.append(RecurringSignal(
            kind=kind, current_count=count, previous_count=previous,
            threshold=threshold, trend=trend,
        ))
    return RecurringSignalReport(
        generated_at=current.isoformat(timespec="seconds"),
        window_days=window_days,
        signals=tuple(signals),
    )
