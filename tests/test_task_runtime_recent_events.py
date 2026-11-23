import pytest

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


def test_recent_task_events_requires_task_read_and_owner_access(tmp_path):
    store = WorkflowRunStore(str(tmp_path / "workflow.sqlite3"))
    run = store.create(
        idempotency_key="authorized-recent-events",
        workflow_name="event-check",
        definition=[{"name": "review", "action": "emit", "argument": "check this", "requires_approval": True}],
        owner_id="owner-a",
        initial_event={
            "kind": "task.created", "actor_id": "owner-a", "payload": {},
        },
    )
    runtime = TaskRuntime(
        store,
        authorize=lambda actor, action, task_id: (
            action == "task:read" and actor == "owner-a"
        ),
    )
    events = runtime.recent_events(actor_id="owner-a", task_id=run.run_id, limit=10)
    assert len(events) == 1
    assert events[0].kind == "task.created"
    with pytest.raises(PermissionError):
        runtime.recent_events(actor_id="owner-b", task_id=run.run_id, limit=10)
