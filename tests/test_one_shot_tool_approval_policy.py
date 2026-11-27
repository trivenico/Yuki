"""Tests for one-shot grant consumption at the guarded dispatch boundary."""
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.guarded_tool_runtime import GuardedToolRuntime
from core.one_shot_tool_approval_policy import OneShotToolApprovalPolicy
from core.tool_action import build_tool_action
from core.tool_action_approvals import SQLiteToolApprovalStore


def echo(argument):
    return argument


class OneShotToolApprovalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = SQLiteToolApprovalStore(str(root / "grants.sqlite3"))
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.calls = []
        def tracked_echo(argument):
            self.calls.append(argument)
            return argument
        self.handler = tracked_echo
        self.argument = '{"path":"notes.txt"}'
        self.action = build_tool_action(
            "files.read", self.argument, self.handler,
            owner_id="owner", session_id="session-a",
        )
        self.grant = self.store.issue(self.action, target_scope="workspace:notes")

    def tearDown(self):
        self.tmp.cleanup()

    def make_runtime(self, *, policy=None, owner="owner", session="session-a"):
        policy = policy or OneShotToolApprovalPolicy(
            self.store, approval_id=self.grant,
            target_scope="workspace:notes", audit_log=self.audit,
        )
        return GuardedToolRuntime(
            {"files.read": self.handler},
            policy, owner_id=owner, session_id=session,
        )

    def test_exact_action_grant_is_consumed_before_handler(self):
        runtime = self.make_runtime()
        self.assertEqual(runtime.invoke("files.read", self.argument), self.argument)
        self.assertEqual(self.calls, [self.argument])
        events = list(reversed(self.audit.list_events()))
        self.assertEqual([event["event_type"] for event in events], ["approved"])
        self.assertFalse(self.store.consume(
            self.grant, self.action, target_scope="workspace:notes"
        ))

    def test_mismatched_arguments_do_not_execute_or_consume_valid_grant(self):
        runtime = self.make_runtime()
        self.assertIn("blocked", runtime.invoke("files.read", '{"path":"secrets.txt"}'))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.store.consume(
            self.grant, self.action, target_scope="workspace:notes"
        ))

    def test_policy_is_single_invocation_even_if_grant_store_is_repopulated(self):
        policy = OneShotToolApprovalPolicy(
            self.store, approval_id=self.grant,
            target_scope="workspace:notes", audit_log=self.audit,
        )
        runtime = self.make_runtime(policy=policy)
        self.assertEqual(runtime.invoke("files.read", self.argument), self.argument)
        replacement = self.store.issue(self.action, target_scope="workspace:notes")
        self.assertIn("blocked", runtime.invoke("files.read", self.argument))
        self.assertEqual(self.calls, [self.argument])
        self.assertTrue(self.store.consume(
            replacement, self.action, target_scope="workspace:notes"
        ))

    def test_audit_failure_denies_dispatch_and_grant_is_not_restored(self):
        class BrokenAudit(SQLiteApprovalAuditLog):
            def append(self, **_kwargs):
                raise OSError("disk unavailable")

        broken_audit = BrokenAudit(str(Path(self.tmp.name) / "broken-audit.sqlite3"))
        policy = OneShotToolApprovalPolicy(
            self.store, approval_id=self.grant,
            target_scope="workspace:notes", audit_log=broken_audit,
        )
        runtime = self.make_runtime(policy=policy)
        self.assertIn("blocked", runtime.invoke("files.read", self.argument))
        self.assertEqual(self.calls, [])
        self.assertFalse(self.store.consume(
            self.grant, self.action, target_scope="workspace:notes"
        ))

    def test_wrong_owner_or_session_cannot_dispatch(self):
        self.assertIn("blocked", self.make_runtime(owner="intruder").invoke("files.read", self.argument))
        self.assertIn("blocked", self.make_runtime(session="session-b").invoke("files.read", self.argument))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.store.consume(
            self.grant, self.action, target_scope="workspace:notes"
        ))

    def test_constructor_rejects_untrusted_store_or_audit_objects(self):
        with self.assertRaises(TypeError):
            OneShotToolApprovalPolicy(object(), approval_id="grant",
                                      target_scope="scope", audit_log=self.audit)
        with self.assertRaises(TypeError):
            OneShotToolApprovalPolicy(self.store, approval_id="grant",
                                      target_scope="scope", audit_log=object())


if __name__ == "__main__":
    unittest.main()
