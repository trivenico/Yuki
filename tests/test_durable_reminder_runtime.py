from datetime import datetime, timezone
from types import SimpleNamespace

from core.durable_reminder_runtime import DurableReminderRuntime


class FakeQueue:
    def __init__(self):
        self.items = []

    def add(self, text, due_at):
        item = {"id": str(len(self.items) + 1), "text": text, "due_at": due_at.isoformat()}
        self.items.append(item)
        return item


class FakeCoordinator:
    def __init__(self, *, approve=True):
        self.approve_request = approve
        self.action = None

    def record(self, planner, result, *, actor, session_id):
        self.action = result["action"]
        return SimpleNamespace(pending_id="pending-1")

    def approve(self, executor, pending_id, *, actor, session_id, confirm):
        if not confirm(self.action):
            return SimpleNamespace(status="approval_denied", result=None)
        result = executor.execute_approved_action(self.action)
        return SimpleNamespace(status="completed", result=result) if self.approve_request else SimpleNamespace(status="approval_denied", result=None)


def test_reminder_add_requires_durable_coordinator_and_executes_approved_snapshot():
    queue = FakeQueue()
    coordinator = FakeCoordinator()
    runtime = DurableReminderRuntime(
        reminders=queue, coordinator=coordinator, actor="test-owner", confirm=lambda action: True
    )
    due = datetime(2027, 1, 1, tzinfo=timezone.utc)
    result = runtime.add("  ship release  ", due)
    assert result.ok is True
    assert len(queue.items) == 1
    assert queue.items[0]["text"] == "ship release"


def test_reminder_denial_does_not_mutate_queue():
    queue = FakeQueue()
    runtime = DurableReminderRuntime(
        reminders=queue, coordinator=FakeCoordinator(), actor="test-owner", confirm=lambda action: False
    )
    result = runtime.add("do not save", datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert result.ok is False
    assert queue.items == []


def test_reminder_rejects_naive_datetime_before_coordinator():
    queue = FakeQueue()
    runtime = DurableReminderRuntime(
        reminders=queue, coordinator=FakeCoordinator(), actor="test-owner", confirm=lambda action: True
    )
    result = runtime.add("invalid time", datetime(2027, 1, 1))
    assert result.ok is False
    assert result.stage == "invalid_input"
    assert queue.items == []


def test_approved_action_rejects_unexpected_payload_keys():
    runtime = DurableReminderRuntime(
        reminders=FakeQueue(), coordinator=FakeCoordinator(), actor="test-owner", confirm=lambda action: True
    )
    try:
        runtime.execute_approved_action({"skill_id": "reminder:add", "arguments": {"text": "x", "due_at": "2027-01-01T00:00:00+00:00", "extra": True}})
    except ValueError:
        pass
    else:
        raise AssertionError("unexpected payload keys must be rejected")
