"""Deterministic, privacy-preserving operational summaries.

This module turns bounded task metadata and allowlisted event kinds into
daily/weekly counts. It deliberately does not inspect event payloads, task
results, definitions, or worker secrets, and it never performs actions.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Any
from itertools import islice

_ALLOWED_EVENTS = frozenset({
    "task.failed", "task.blocked", "task.interrupted", "task.completed",
})
_MAX_TASKS = 500
_MAX_EVENTS = 5_000


@dataclass(frozen=True)
class OperationalSummary:
    period: str
    started_at: str
    ended_at: str
    tasks_observed: int
    task_states: dict[str, int]
    event_counts: dict[str, int]
    attention_items: tuple[str, ...]

    def render(self) -> str:
        lines = [
            f"Yuki {self.period.title()} Operational Summary",
            f"Window: {self.started_at} — {self.ended_at}",
            f"Tasks observed: {self.tasks_observed}",
            "Task states: " + (", ".join(
                f"{key}={value}" for key, value in sorted(self.task_states.items())
            ) or "none"),
            "Events: " + (", ".join(
                f"{key}={value}" for key, value in sorted(self.event_counts.items())
            ) or "none"),
            "Evidence-based attention:",
        ]
        lines.extend(f"- {item}" for item in self.attention_items)
        if not self.attention_items:
            lines.append("- No threshold-based attention items in this window.")
        lines.append("This summary is observational; no tasks or settings were changed.")
        return "\n".join(lines)


def build_operational_summary(
    *, tasks: Iterable[Any], events: Iterable[Any], period: str = "daily",
    now: datetime | None = None,
) -> OperationalSummary:
    """Build a UTC daily (24h) or weekly (7d) summary from bounded records.

    Tasks must expose state. Events must expose kind and created_at. Other
    fields—including payload and result—are never accessed.
    """
    if period not in {"daily", "weekly"}:
        raise ValueError("period must be 'daily' or 'weekly'.")
    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime.")
    current = current.astimezone(timezone.utc)
    start = current - (timedelta(days=1) if period == "daily" else timedelta(days=7))

    task_rows = list(islice(tasks, _MAX_TASKS + 1))
    event_rows = list(islice(events, _MAX_EVENTS + 1))
    if len(task_rows) > _MAX_TASKS:
        raise ValueError("tasks exceeds the 500-record summary limit.")
    if len(event_rows) > _MAX_EVENTS:
        raise ValueError("events exceeds the 5000-record summary limit.")

    states: Counter[str] = Counter()
    for task in task_rows:
        state = getattr(task, "state", "other")
        if not isinstance(state, str) or len(state) > 40:
            state = "other"
        states[state if state in {
            "queued", "waiting_approval", "running", "succeeded",
            "failed", "cancelled", "interrupted", "blocked",
        } else "other"] += 1

    event_counts: Counter[str] = Counter()
    for event in event_rows:
        kind = getattr(event, "kind", None)
        if kind not in _ALLOWED_EVENTS:
            continue
        raw_time = getattr(event, "created_at", None)
        try:
            timestamp = datetime.fromisoformat(raw_time)
            if timestamp.tzinfo is None:
                continue
            timestamp = timestamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if start < timestamp <= current:
            event_counts[kind] += 1

    attention: list[str] = []
    if event_counts["task.failed"]:
        attention.append(
            f"{event_counts['task.failed']} task failure event(s) recorded; review their task histories."
        )
    if event_counts["task.blocked"]:
        attention.append(
            f"{event_counts['task.blocked']} blocked event(s) recorded; inspect approval or dependency bottlenecks."
        )
    if event_counts["task.interrupted"]:
        attention.append(
            f"{event_counts['task.interrupted']} interrupted event(s) recorded; verify worker and lease health."
        )
    if states["running"] and states["failed"]:
        attention.append(
            "Running and failed tasks coexist; review capacity and failure handling before increasing concurrency."
        )

    return OperationalSummary(
        period=period,
        started_at=start.isoformat(timespec="seconds"),
        ended_at=current.isoformat(timespec="seconds"),
        tasks_observed=len(task_rows),
        task_states=dict(states),
        event_counts=dict(event_counts),
        attention_items=tuple(attention),
    )
