from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import pytest

from core.proactive_awareness import build_operational_awareness


@dataclass
class Task:
    task_id: str
    name: str
    state: str
    lease_expires_at: str | None = None
    result: object = None


@dataclass
class Worker:
    worker_id: str
    status: str
    metadata: dict | None = None


def test_builds_operational_snapshot_and_flags_expired_leases():
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    tasks = [
        Task("t-running", "Build", "running", (now - timedelta(seconds=1)).isoformat(),
             result={"private": "must not leak"}),
        Task("t-queued", "Research", "queued"),
        Task("t-failed", "Deploy", "failed"),
    ]
    workers = [
        Worker("worker-a", "active", {"secret": "must not leak"}),
        Worker("worker-b", "stale"),
    ]
    result = build_operational_awareness(
        current_goal="Finish Yuki safely", tasks=tasks, workers=workers, now=now,
    )
    assert result.snapshot.mode == "background"
    assert result.snapshot.uncertainty == "medium"
    assert any("expired worker lease" in item for item in result.attention_items)
    assert any("is failed" in item for item in result.attention_items)
    assert any("worker-b is stale" in item for item in result.attention_items)
    rendered = " ".join(result.snapshot.known_context + result.snapshot.recent_events)
    assert "must not leak" not in rendered
    assert "worker-a" not in rendered


def test_empty_snapshot_reports_unknown_confidence():
    result = build_operational_awareness(current_goal="", tasks=[], workers=[])
    assert result.snapshot.uncertainty == "unknown"
    assert result.attention_items == ()


@pytest.mark.parametrize("tasks,workers", [
    ([Task("x", "name", "unknown")], []),
    ([], [Worker("x", "broken")]),
    ([Task("x", "name", "running", "not-a-date")], []),
])
def test_rejects_invalid_operational_observations(tasks, workers):
    with pytest.raises(ValueError):
        build_operational_awareness(current_goal="goal", tasks=tasks, workers=workers)


def test_bounds_snapshot_sizes():
    with pytest.raises(ValueError):
        build_operational_awareness(
            current_goal="goal",
            tasks=[Task(str(i), "task", "queued") for i in range(101)],
            workers=[],
        )
