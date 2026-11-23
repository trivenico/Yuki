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



class _TaskReader:
    def __init__(self):
        self.called_as = None

    def list_task_summaries(self, *, actor_id, limit):
        self.called_as = (actor_id, limit)
        return [Task("owned-task", "My task", "queued")]


class _WorkerReader:
    def __init__(self):
        self.called = False

    def list_workers(self, *, stale_after_seconds, limit):
        self.called = True
        assert stale_after_seconds == 60
        assert limit == 100
        return [Worker("worker", "active")]


def test_service_requires_worker_health_authorization_and_uses_task_runtime_acl():
    from core.proactive_awareness import OperationalAwarenessService

    tasks, workers = _TaskReader(), _WorkerReader()
    service = OperationalAwarenessService(
        tasks, workers, authorize_worker_health=lambda actor: actor == "admin",
    )
    with pytest.raises(PermissionError):
        service.snapshot(actor_id="member", current_goal="goal")
    assert tasks.called_as is None
    assert not workers.called

    result = service.snapshot(actor_id="admin", current_goal="goal")
    assert tasks.called_as == ("admin", 100)
    assert workers.called
    assert result.snapshot.current_goal == "goal"


def test_service_fails_closed_when_task_reader_rejects_actor():
    from core.proactive_awareness import OperationalAwarenessService

    class DeniedTasks:
        def list_task_summaries(self, *, actor_id, limit):
            raise PermissionError("task listing denied")

    service = OperationalAwarenessService(
        DeniedTasks(), _WorkerReader(), authorize_worker_health=lambda actor: True,
    )
    with pytest.raises(PermissionError):
        service.snapshot(actor_id="member", current_goal="goal")



def test_bounds_generator_consumption_without_materializing_unbounded_input():
    consumed = []

    def endless_tasks():
        index = 0
        while True:
            consumed.append(index)
            yield Task(str(index), "task", "queued")
            index += 1

    with pytest.raises(ValueError):
        build_operational_awareness(
            current_goal="goal", tasks=endless_tasks(), workers=[],
        )
    assert len(consumed) == 101


def test_bounds_snapshot_sizes():
    with pytest.raises(ValueError):
        build_operational_awareness(
            current_goal="goal",
            tasks=[Task(str(i), "task", "queued") for i in range(101)],
            workers=[],
        )
