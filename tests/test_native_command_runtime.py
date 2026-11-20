import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.task_runtime import TaskRuntime
from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory
from core.approval_audit import SQLiteApprovalAuditLog
from core.native_command_runtime import NativeCommandRuntime
from core.native_intents import IntentKind


class NativeCommandRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.task_runtime = Mock(spec=TaskRuntime)
        self.task_runtime.list_task_summaries.return_value = []
        self.memory = CompanionMemory(MemoryStore(root / "owner.json"))
        self.memory.enable()
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.runtime = NativeCommandRuntime(
            task_runtime=self.task_runtime,
            memory_for_actor=lambda actor: self.memory if actor == "owner-1" else CompanionMemory(
                MemoryStore(root / "other.json")
            ),
            audit_log=self.audit,
            authorize_capability=lambda actor, capability: actor == "owner-1",
            status_provider=lambda actor: {"ready": True, "actor": actor, "api_key": "never expose"},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_help_and_status_are_wired_and_audited(self):
        help_result = self.runtime.dispatch(authenticated_actor_id="owner-1", text="/help")
        status_result = self.runtime.dispatch(authenticated_actor_id="owner-1", text="/status")
        self.assertIn("/task list", help_result.output)
        self.assertTrue(json.loads(status_result.output)["ready"])
        self.assertNotIn("api_key", status_result.output)
        self.assertNotIn("actor", status_result.output)
        self.assertEqual(len(self.audit.list_events()), 2)
        self.assertTrue(self.audit.verify_chain())

    def test_task_list_uses_task_adapter(self):
        result = self.runtime.dispatch(authenticated_actor_id="owner-1", text="/task list")
        self.assertEqual(result.kind, IntentKind.TASK_LIST)
        self.task_runtime.list_task_summaries.assert_called_once()

    def test_memory_search_and_remember_use_consent_facade(self):
        self.memory.remember("native Yuki memory architecture")
        result = self.runtime.dispatch(authenticated_actor_id="owner-1", text="/memory search native")
        self.assertIn("architecture", result.output)
        saved = self.runtime.dispatch(
            authenticated_actor_id="owner-1",
            text="/memory remember explicit native fact",
            confirmed=True,
        )
        self.assertTrue(json.loads(saved.output)["saved"])

    def test_remember_requires_confirmation_before_audit_or_write(self):
        before = len(self.memory.list_memories())
        with self.assertRaises(PermissionError):
            self.runtime.dispatch(authenticated_actor_id="owner-1", text="/memory remember not-confirmed")
        self.assertEqual(len(self.memory.list_memories()), before)
        self.assertEqual(self.audit.list_events(), [])

    def test_denied_capability_does_not_write_audit_or_run_handler(self):
        with self.assertRaises(PermissionError):
            self.runtime.dispatch(authenticated_actor_id="intruder", text="/task list")
        self.task_runtime.list_task_summaries.assert_not_called()
        self.assertEqual(self.audit.list_events(), [])

    def test_audit_failure_fails_closed(self):
        broken = Mock(spec=SQLiteApprovalAuditLog)
        broken.append.side_effect = OSError("disk full")
        runtime = NativeCommandRuntime(
            task_runtime=self.task_runtime,
            memory_for_actor=lambda _: self.memory,
            audit_log=broken,
            authorize_capability=lambda *_: True,
        )
        with self.assertRaises(RuntimeError):
            runtime.dispatch(authenticated_actor_id="owner-1", text="/task list")
        self.task_runtime.list_task_summaries.assert_not_called()

    def test_unknown_and_unsupported_plan_commands_do_not_execute(self):
        unknown = self.runtime.dispatch(authenticated_actor_id="owner-1", text="delete everything")
        self.assertFalse(unknown.executed)
        plan = self.runtime.dispatch(authenticated_actor_id="owner-1", text="/plan deploy")
        self.assertFalse(plan.executed)

    def test_actor_id_validation(self):
        for actor in ("", "owner\nforged", "x" * 201):
            with self.assertRaises(ValueError):
                self.runtime.dispatch(authenticated_actor_id=actor, text="/help")


if __name__ == "__main__":
    unittest.main()
