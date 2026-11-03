import logging
import tempfile
import unittest
from pathlib import Path

from core.logging_setup import configure_logging, log_event
from core.agent import YukiCoreAgent
from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore
from core.permissions import PermissionGate


class PhaseTwoLoggingAuditTests(unittest.TestCase):
    @staticmethod
    def _close_logger():
        logger = logging.getLogger("yuki")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def tearDown(self):
        self._close_logger()

    def test_rotating_logger_writes_utf8_and_sanitizes_line_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                logger = configure_logging(tmp)
                log_event(logger, logging.INFO, "started\nforged", component="runtime")
                for handler in logger.handlers:
                    handler.flush()
                text = (Path(tmp) / "yuki.log").read_text(encoding="utf-8")
                self.assertIn("event='started forged'", text)
                self.assertEqual(len(text.splitlines()), 1)
                self.assertFalse(logger.propagate)
            finally:
                # Windows cannot remove a temporary directory while its log file is open.
                self._close_logger()

    def test_logger_configuration_is_idempotent_for_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                first = configure_logging(tmp)
                count = len(first.handlers)
                second = configure_logging(tmp)
                self.assertIs(first, second)
                self.assertEqual(len(second.handlers), count)
            finally:
                self._close_logger()

    def test_permission_gate_audits_only_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            audit = SQLiteApprovalAuditLog(path)
            gate = PermissionGate(lambda request: True, audit_log=audit)
            self.assertTrue(gate.request("skill:notes.write:secret-args", "private prompt text", "high"))
            events = audit.list_events()
            self.assertEqual([row["event_type"] for row in reversed(events)], ["requested", "approved"])
            with open(path, "rb") as stream:
                data = stream.read()
            self.assertNotIn(b"secret-args", data)
            self.assertNotIn(b"private prompt text", data)
            self.assertTrue(audit.verify_chain())

    def test_real_agent_execution_writes_correlated_terminal_audit_event(self):
        class EchoSkill:
            name = "echo"
            description = "Echo approved input"
            risk = "low"
            requires_approval = True

            def __init__(self):
                self.calls = []

            def execute(self, argument):
                self.calls.append(argument)
                return "done"

        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            skill = EchoSkill()
            agent = YukiCoreAgent(
                skills=[skill],
                permission_gate=PermissionGate(lambda _request: True, audit_log=audit),
            )
            result = agent.run_skill("echo", "safe input")
            self.assertTrue(result.ok)
            self.assertEqual(result.stage, "report")
            self.assertEqual(skill.calls, ["safe input"])
            events = list(reversed(audit.list_events()))
            self.assertEqual(
                [event["event_type"] for event in events],
                ["requested", "approved", "executed"],
            )
            self.assertEqual(len({event["pending_id_hash"] for event in events}), 1)
            self.assertEqual(len({event["session_hash"] for event in events}), 1)
            self.assertTrue(all(event["session_hash"] for event in events))
            self.assertTrue(audit.verify_chain())

    def test_agent_uses_durable_coordinator_for_real_skill_execution(self):
        class EchoSkill:
            name = "echo"
            description = "Echo approved input"
            risk = "low"
            requires_approval = True

            def __init__(self):
                self.calls = []

            def execute(self, argument):
                self.calls.append(argument)
                return "done"

        class RecordingStore(SQLitePendingApprovalStore):
            pending_id = None

            def put(self, *args, **kwargs):
                item = super().put(*args, **kwargs)
                self.pending_id = item.pending_id
                return item

        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            store = RecordingStore(str(Path(tmp) / "pending.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
            confirmations = []
            skill = EchoSkill()
            agent = YukiCoreAgent(
                skills=[skill],
                permission_gate=PermissionGate(
                    lambda request: confirmations.append(request) or True,
                    actor_id="test-actor",
                    session_id="test-session",
                ),
                approval_coordinator=coordinator,
            )

            result = agent.run_skill("echo", "durably approved input")

            self.assertTrue(result.ok)
            self.assertEqual(result.stage, "report")
            self.assertEqual(skill.calls, ["durably approved input"])
            self.assertEqual(len(confirmations), 1)
            self.assertEqual(store.get_status(store.pending_id), "completed")
            events = list(reversed(audit.list_events()))
            self.assertEqual(
                [event["event_type"] for event in events],
                ["requested", "consumed", "approved", "executed"],
            )
            self.assertEqual(len({event["pending_id_hash"] for event in events}), 1)
            self.assertEqual(len({event["session_hash"] for event in events}), 1)
            self.assertTrue(audit.verify_chain())

    def test_durable_coordinator_denial_never_executes_skill(self):
        class EchoSkill:
            name = "echo"
            description = "Echo approved input"
            risk = "low"
            requires_approval = True

            def __init__(self):
                self.calls = []

            def execute(self, argument):
                self.calls.append(argument)
                return "done"

        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
            skill = EchoSkill()
            agent = YukiCoreAgent(
                skills=[skill],
                permission_gate=PermissionGate(lambda _request: False),
                approval_coordinator=coordinator,
            )

            result = agent.run_skill("echo", "must not execute")

            self.assertFalse(result.ok)
            self.assertEqual(result.stage, "permission_denied")
            self.assertEqual(skill.calls, [])
            self.assertEqual(
                [event["event_type"] for event in reversed(audit.list_events())],
                ["requested", "consumed", "denied"],
            )
            self.assertTrue(audit.verify_chain())

    def test_recovery_queue_lists_interrupted_actions_without_full_bearer_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
            item = store.put(
                "files.write",
                {"argument": "approved snapshot"},
                {"skill_id": "files.write", "arguments": {"argument": "approved snapshot"}},
                actor="local-cli",
                session_id="cli-session",
            )
            claimed = store.claim(
                item.pending_id, actor="local-cli", session_id="cli-session"
            )
            self.assertIsNotNone(claimed)
            self.assertTrue(store.transition(
                item.pending_id, expected="claimed", new_status="recovery_required"
            ))
            agent = YukiCoreAgent(
                skills=[],
                permission_gate=PermissionGate(lambda _request: False),
                approval_coordinator=coordinator,
            )

            records = agent.list_approval_recovery()

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["reference"], item.pending_id[-12:])
            self.assertNotIn(item.pending_id, str(records))
            self.assertEqual(records[0]["skill_id"], "files.write")
            self.assertEqual(records[0]["status"], "recovery_required")
            self.assertEqual(store.get_status(item.pending_id), "recovery_required")

    def test_operator_can_resolve_recovery_without_reexecuting_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
            item = store.put(
                "files.write", {"argument": "redacted"},
                {"skill_id": "files.write", "arguments": {"argument": "redacted"}},
                actor="local-cli", session_id="session-1",
            )
            self.assertIsNotNone(store.claim(
                item.pending_id, actor="local-cli", session_id="session-1"
            ))
            self.assertTrue(store.transition(
                item.pending_id, expected="claimed", new_status="recovery_required"
            ))
            confirmations = []
            agent = YukiCoreAgent(
                skills=[],
                permission_gate=PermissionGate(
                    lambda request: confirmations.append(request) or True,
                    actor_id="operator-1", session_id="operator-session",
                ),
                approval_coordinator=coordinator,
            )

            resolved = agent.resolve_approval_recovery(
                item.pending_id[-12:], "completed"
            )

            self.assertTrue(resolved)
            self.assertEqual(store.get_status(item.pending_id), "reconciled_completed")
            self.assertEqual(len(confirmations), 1)
            self.assertIn("does not execute or retry", confirmations[0].reason)
            events = list(reversed(audit.list_events()))
            self.assertEqual(events[-1]["event_type"], "reconciliation_completed")
            self.assertTrue(audit.verify_chain())

    def test_uncertain_recovery_decision_remains_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
            item = store.put(
                "files.write", {"argument": "redacted"},
                {"skill_id": "files.write", "arguments": {"argument": "redacted"}},
                actor="local-cli", session_id="session-1",
            )
            store.claim(item.pending_id, actor="local-cli", session_id="session-1")
            store.transition(item.pending_id, expected="claimed", new_status="recovery_required")
            agent = YukiCoreAgent(
                skills=[],
                permission_gate=PermissionGate(lambda _request: True),
                approval_coordinator=coordinator,
            )

            self.assertTrue(agent.resolve_approval_recovery(item.pending_id[-12:], "uncertain"))
            self.assertEqual(store.get_status(item.pending_id), "recovery_required")
            self.assertEqual(len(agent.list_approval_recovery()), 1)
            self.assertEqual(audit.list_events()[0]["event_type"], "reconciliation_uncertain")

    def test_reconciliation_reservation_is_atomic_and_identity_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            item = store.put(
                "files.write", {"argument": "x"},
                {"skill_id": "files.write", "arguments": {"argument": "x"}},
                actor="creator", session_id="creator-session",
            )
            store.claim(item.pending_id, actor="creator", session_id="creator-session")
            store.transition(item.pending_id, expected="claimed", new_status="recovery_required")

            self.assertTrue(store.begin_reconciliation(
                item.pending_id, decision="completed", actor="operator-a", session_id="session-a"
            ))
            self.assertTrue(store.begin_reconciliation(
                item.pending_id, decision="completed", actor="operator-a", session_id="session-a"
            ))
            self.assertFalse(store.begin_reconciliation(
                item.pending_id, decision="failed", actor="operator-a", session_id="session-a"
            ))
            self.assertFalse(store.begin_reconciliation(
                item.pending_id, decision="completed", actor="operator-b", session_id="session-b"
            ))
            self.assertEqual(store.list_recovery_required()[0]["status"], "reconciling")
            self.assertTrue(store.finish_reconciliation(item.pending_id, decision="completed"))
            self.assertEqual(store.get_status(item.pending_id), "reconciled_completed")
            self.assertFalse(store.finish_reconciliation(item.pending_id, decision="completed"))

    def test_reconciliation_can_resume_after_audit_failure_without_duplicate_event(self):
        class FailOnceAudit:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.fail = True

            def append(self, **kwargs):
                if self.fail:
                    self.fail = False
                    raise OSError("simulated crash before audit append")
                return self.wrapped.append(**kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            item = store.put(
                "files.write", {"argument": "x"},
                {"skill_id": "files.write", "arguments": {"argument": "x"}},
                actor="creator", session_id="creator-session",
            )
            store.claim(item.pending_id, actor="creator", session_id="creator-session")
            store.transition(item.pending_id, expected="claimed", new_status="recovery_required")
            wrapped = FailOnceAudit(audit)
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=wrapped)
            confirmations = []
            agent = YukiCoreAgent(
                skills=[],
                permission_gate=PermissionGate(
                    lambda request: confirmations.append(request) or True,
                    actor_id="operator-a", session_id="session-a",
                ),
                approval_coordinator=coordinator,
            )

            self.assertFalse(agent.resolve_approval_recovery(item.pending_id[-12:], "completed"))
            self.assertEqual(store.get_status(item.pending_id), "reconciling")
            self.assertEqual(store.list_recovery_required()[0]["status"], "reconciling")
            self.assertTrue(agent.resolve_approval_recovery(item.pending_id[-12:], "completed"))
            self.assertEqual(store.get_status(item.pending_id), "reconciled_completed")
            events = [event for event in audit.list_events() if event["event_type"] == "reconciliation_completed"]
            self.assertEqual(len(events), 1)
            self.assertTrue(audit.verify_chain())

    def test_recovery_resolution_requires_operator_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
            item = store.put(
                "files.write", {"argument": "redacted"},
                {"skill_id": "files.write", "arguments": {"argument": "redacted"}},
                actor="local-cli", session_id="session-1",
            )
            store.claim(item.pending_id, actor="local-cli", session_id="session-1")
            store.transition(item.pending_id, expected="claimed", new_status="recovery_required")
            agent = YukiCoreAgent(
                skills=[], permission_gate=PermissionGate(lambda _request: False),
                approval_coordinator=coordinator,
            )

            self.assertFalse(agent.resolve_approval_recovery(item.pending_id[-12:], "failed"))
            self.assertEqual(store.get_status(item.pending_id), "recovery_required")
            self.assertEqual(audit.list_events(), [])

    def test_recovery_queue_is_empty_without_durable_store(self):
        agent = YukiCoreAgent(skills=[])
        self.assertEqual(agent.list_approval_recovery(), [])

    def test_agent_surfaces_recovery_when_terminal_audit_fails_after_execution(self):
        class EchoSkill:
            name = "echo"
            description = "Echo approved input"
            risk = "low"
            requires_approval = True

            def __init__(self):
                self.calls = []

            def execute(self, argument):
                self.calls.append(argument)
                return "done"

        class FailTerminalAudit:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def append(self, *, event_type, **event):
                if event_type == "executed":
                    raise OSError("audit unavailable")
                return self.wrapped.append(event_type=event_type, **event)

        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            skill = EchoSkill()
            agent = YukiCoreAgent(
                skills=[skill],
                permission_gate=PermissionGate(
                    lambda _request: True, audit_log=FailTerminalAudit(audit)
                ),
            )
            result = agent.run_skill("echo", "possibly completed")
            self.assertFalse(result.ok)
            self.assertEqual(result.stage, "recovery_required")
            self.assertEqual(skill.calls, ["possibly completed"])
            self.assertEqual(
                [event["event_type"] for event in reversed(audit.list_events())],
                ["requested", "approved"],
            )
            self.assertTrue(audit.verify_chain())

    def test_audit_idempotency_key_prevents_duplicate_reconciliation_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            kwargs = {
                "event_type": "reconciliation_completed",
                "pending_id": "receipt-for-idempotency",
                "actor": "operator",
                "session_id": "operator-session",
                "skill_id": "files.write",
                "idempotency_key": "reconcile-key-001",
            }
            first = audit.append(**kwargs)
            second = audit.append(**kwargs)

            self.assertEqual(first, second)
            self.assertEqual(len(audit.list_events()), 1)
            self.assertEqual(audit.list_events()[0]["event_key"], "reconcile-key-001")
            self.assertTrue(audit.verify_chain())

            with self.assertRaises(ValueError):
                audit.append(**{**kwargs, "skill_id": "different.skill"})
            with self.assertRaises(ValueError):
                audit.append(**{**kwargs, "actor": "different-operator"})
            self.assertEqual(len(audit.list_events()), 1)
            self.assertTrue(audit.verify_chain())

    def test_audit_failure_prevents_approval(self):
        class BrokenAudit:
            def append(self, **kwargs):
                raise OSError("storage unavailable")
        called = []
        gate = PermissionGate(lambda request: called.append(True) or True, audit_log=BrokenAudit())
        self.assertFalse(gate.request("safe-action", "reason", "low"))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
