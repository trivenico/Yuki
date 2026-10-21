"""Integration tests using the real SQLite approval, audit, and execution stores."""
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.durable_knowledge_runtime import DurableKnowledgeRuntime
from core.execution_ledger import SQLiteExecutionLedger
from core.pending_approvals import SQLitePendingApprovalStore


class RecordingIndexer:
    def __init__(self):
        self.calls = []

    def index_file(self, relative_path, *, workspace):
        self.calls.append((relative_path, Path(workspace)))
        return {"indexed": relative_path}


def make_runtime(tmp_path, *, confirm=lambda action: True, audit=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.md").write_text("hello", encoding="utf-8")
    pending = SQLitePendingApprovalStore(str(tmp_path / "pending.sqlite3"))
    audit_log = audit or SQLiteApprovalAuditLog(str(tmp_path / "audit.sqlite3"))
    ledger = SQLiteExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    coordinator = ApprovalCoordinator(
        pending_store=pending, audit_log=audit_log, execution_ledger=ledger
    )
    indexer = RecordingIndexer()
    runtime = DurableKnowledgeRuntime(
        indexer=indexer, workspace=workspace, coordinator=coordinator,
        actor="integration-test-owner", confirm=confirm, session_id="integration-session",
    )
    return runtime, indexer, audit_log, ledger


def test_knowledge_indexing_uses_real_durable_stores(tmp_path):
    runtime, indexer, audit, ledger = make_runtime(tmp_path)
    result = runtime.index("notes.md")
    assert result["ok"] is True
    assert indexer.calls == [("notes.md", (tmp_path / "workspace").resolve())]
    assert audit.verify_chain() is True
    events = [item["event_type"] for item in audit.list_events()]
    assert "requested" in events
    assert "approved" in events
    assert "executed" in events
    records = [ledger.get(key) for key in ("missing",)]
    assert records == [None]


def test_real_coordinator_denial_does_not_index_or_create_execution(tmp_path):
    runtime, indexer, audit, ledger = make_runtime(tmp_path, confirm=lambda action: False)
    result = runtime.index("notes.md")
    assert result["ok"] is False
    assert result["status"] == "approval_denied"
    assert indexer.calls == []
    assert audit.verify_chain() is True
    assert "denied" in [item["event_type"] for item in audit.list_events()]


class FailingAudit:
    def append(self, **kwargs):
        raise OSError("audit storage unavailable")


def test_audit_failure_fails_closed_with_real_pending_store(tmp_path):
    runtime, indexer, _, _ = make_runtime(tmp_path, audit=FailingAudit())
    result = runtime.index("notes.md")
    assert result["ok"] is False
    assert result["status"] == "recovery_required"
    assert indexer.calls == []
