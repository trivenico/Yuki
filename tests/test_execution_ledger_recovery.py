"""Crash-recovery and idempotency regression tests for the durable execution ledger."""
import pytest

from core.execution_ledger import SQLiteExecutionLedger


def test_running_execution_survives_reopen_and_requires_reconciliation(tmp_path):
    database = tmp_path / "execution.sqlite3"
    action = {"skill_id": "knowledge:index", "arguments": {"relative_path": "notes.md"}}

    first_process = SQLiteExecutionLedger(str(database))
    record, created = first_process.begin("approval-123", action)
    assert created is True
    assert record.state == "prepared"
    first_process.transition("approval-123", "running")

    # A new ledger instance models a process restart against the same durable DB.
    restarted_process = SQLiteExecutionLedger(str(database))
    persisted = restarted_process.get("approval-123")
    assert persisted is not None
    assert persisted.state == "running"

    pending = restarted_process.list_reconciliation_required()
    assert [item.idempotency_key for item in pending] == ["approval-123"]


def test_reusing_idempotency_key_never_resets_running_state(tmp_path):
    ledger = SQLiteExecutionLedger(str(tmp_path / "execution.sqlite3"))
    action = {"skill_id": "reminder:add", "arguments": {"text": "review", "due_at": "2027-01-01T00:00:00+00:00"}}
    record, created = ledger.begin("approval-456", action)
    assert created is True
    ledger.transition("approval-456", "running")

    existing, created_again = ledger.begin("approval-456", action)
    assert created_again is False
    assert existing.state == "running"
    assert ledger.get("approval-456").state == "running"


def test_idempotency_key_cannot_be_rebound_to_different_action(tmp_path):
    ledger = SQLiteExecutionLedger(str(tmp_path / "execution.sqlite3"))
    ledger.begin("approval-789", {"skill_id": "knowledge:index", "arguments": {"relative_path": "a.md"}})
    with pytest.raises(ValueError, match="different action"):
        ledger.begin("approval-789", {"skill_id": "knowledge:index", "arguments": {"relative_path": "b.md"}})


def test_completed_execution_is_not_listed_for_reconciliation(tmp_path):
    ledger = SQLiteExecutionLedger(str(tmp_path / "execution.sqlite3"))
    action = {"skill_id": "knowledge:index", "arguments": {"relative_path": "notes.md"}}
    ledger.begin("approval-complete", action)
    ledger.transition("approval-complete", "running")
    ledger.transition("approval-complete", "completed", result={"ok": True})

    assert ledger.list_reconciliation_required() == []
