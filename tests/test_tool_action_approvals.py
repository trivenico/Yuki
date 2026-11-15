"""Tests for durable approvals bound to exact trusted tool actions."""
import tempfile
import threading
import unittest
from pathlib import Path

from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_action_context import ToolActionContext


class SQLiteToolActionApprovalStoreTests(unittest.TestCase):
    def make_store(self, directory, **kwargs):
        return SQLiteToolActionApprovalStore(
            str(Path(directory) / "tool-approvals.sqlite3"), **kwargs,
        )

    def test_approval_survives_restart_and_is_single_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = ToolActionContext.create("user-1", "email.send", {"to": "a@example.com"})
            token = self.make_store(tmp).issue(context, principal_id="user-1")
            reopened = self.make_store(tmp)
            self.assertTrue(reopened.consume(token, context, principal_id="user-1"))
            self.assertFalse(reopened.consume(token, context, principal_id="user-1"))

    def test_approval_is_bound_to_principal_action_tool_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            context = ToolActionContext.create("user-1", "email.send", {"to": "a@example.com"})
            token = store.issue(context, principal_id="user-1")
            other_principal = ToolActionContext.create("user-2", "email.send", {"to": "a@example.com"})
            other_tool = ToolActionContext.create("user-1", "calendar.delete", {"to": "a@example.com"})
            other_request = ToolActionContext.create("user-1", "email.send", {"to": "b@example.com"})
            self.assertFalse(store.consume(token, other_principal, principal_id="user-2"))
            self.assertFalse(store.consume(token, other_tool, principal_id="user-1"))
            self.assertFalse(store.consume(token, other_request, principal_id="user-1"))
            self.assertTrue(store.consume(token, context, principal_id="user-1"))

    def test_context_principal_cannot_be_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            context = ToolActionContext.create("user-1", "email.send", {})
            with self.assertRaises(ValueError):
                store.issue(context, principal_id="user-2")

    def test_concurrent_consumption_has_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            context = ToolActionContext.create("user-1", "email.send", {})
            token = store.issue(context, principal_id="user-1")
            barrier = threading.Barrier(3)
            outcomes = []
            lock = threading.Lock()

            def consume():
                barrier.wait()
                result = store.consume(token, context, principal_id="user-1")
                with lock:
                    outcomes.append(result)

            workers = [threading.Thread(target=consume) for _ in range(2)]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(timeout=5)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 1)

    def test_invalid_ttl_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ttl in (0, -1, True, 3601):
                with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                    self.make_store(tmp, ttl_seconds=ttl)



    def test_revocation_is_principal_scoped_and_prevents_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            context = ToolActionContext.create("user-1", "email.send", {"to": "a@example.com"})
            token = store.issue(context, principal_id="user-1")
            self.assertFalse(store.revoke(token, principal_id="user-2"))
            self.assertTrue(store.revoke(token, principal_id="user-1"))
            self.assertFalse(store.consume(token, context, principal_id="user-1"))

    def test_audit_history_records_lifecycle_without_raw_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            context = ToolActionContext.create("user-1", "email.send", {"to": "a@example.com"})
            token = store.issue(context, principal_id="user-1")
            self.assertTrue(store.consume(token, context, principal_id="user-1"))
            events = store.audit_events(principal_id="user-1")
            self.assertEqual([event["outcome"] for event in events], ["consumed", "issued"])
            self.assertTrue(all(token not in str(event) for event in events))
            self.assertEqual(store.audit_events(principal_id="user-2"), ())

    def test_audit_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            with self.assertRaises(ValueError):
                store.audit_events(principal_id="user-1", limit=0)


if __name__ == "__main__":
    unittest.main()
