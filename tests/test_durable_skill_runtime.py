import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.execution_ledger import SQLiteExecutionLedger
from core.pending_approvals import SQLitePendingApprovalStore
from core.durable_skill_runtime import DurableSkillRuntime


class FakeSkill:
    name = "notes.write"
    description = "Write a test note"
    risk = "medium"

    def __init__(self):
        self.calls = []

    def execute(self, argument):
        self.calls.append(argument)
        return {"ok": True, "message": "saved", "path": "note.txt"}


class FakeRegistry:
    def __init__(self):
        self.skill = FakeSkill()

    def get(self, name):
        return self.skill if name == self.skill.name else None

    def describe(self):
        return [{"name": self.skill.name, "description": self.skill.description, "risk": self.skill.risk}]


class DurableSkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = FakeRegistry()
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.ledger = SQLiteExecutionLedger(str(root / "ledger.sqlite3"))
        self.coordinator = ApprovalCoordinator(
            pending_store=SQLitePendingApprovalStore(str(root / "pending.sqlite3")),
            audit_log=self.audit, execution_ledger=self.ledger,
        )
        self.runtime = DurableSkillRuntime(
            registry=self.registry, coordinator=self.coordinator, actor="test-owner",
            session_id="test-session", confirm=lambda _action: True,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_skill_uses_durable_approval_audit_and_ledger(self):
        result = self.runtime.run_skill("notes.write", "hello")
        self.assertTrue(result.ok)
        self.assertEqual(self.registry.skill.calls, ["hello"])
        self.assertEqual(self.audit.list_events()[-1]["event_type"], "executed")

    def test_denied_approval_never_executes_skill(self):
        self.runtime.confirm = lambda _action: False
        result = self.runtime.run_skill("notes.write", "hello")
        self.assertFalse(result.ok)
        self.assertEqual(self.registry.skill.calls, [])

    def test_unregistered_skill_never_executes(self):
        result = self.runtime.run_skill("shell", "id")
        self.assertFalse(result.ok)
        self.assertEqual(self.registry.skill.calls, [])

    def test_execution_ledger_records_completed(self):
        result = self.runtime.run_skill("notes.write", "hello")
        self.assertTrue(result.ok)
        rows = self.ledger.list_records()
        self.assertEqual(rows[0].state, "completed")


if __name__ == "__main__":
    unittest.main()
