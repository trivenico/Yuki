"""Regression tests for durable exact-action tool approvals."""
import tempfile
import threading
import unittest
from pathlib import Path

from core.tool_action import build_tool_action
from core.tool_action_approvals import SQLiteToolApprovalStore, action_approval_digest


def handler_a(argument):
    return argument


def handler_b(argument):
    return argument


class SQLiteToolApprovalStoreTests(unittest.TestCase):
    def make_action(self, argument="{}", handler=handler_a, owner="owner", session="session-a"):
        return build_tool_action(
            "files.read", argument, handler, owner_id=owner, session_id=session
        )

    def make_store(self, directory, **kwargs):
        return SQLiteToolApprovalStore(str(Path(directory) / "approvals.sqlite3"), **kwargs)

    def test_approval_is_one_shot_and_bound_to_exact_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            action = self.make_action('{"path":"notes.txt"}')
            grant = store.issue(action, target_scope="workspace:notes")
            self.assertFalse(store.consume(
                grant, self.make_action('{"path":"secrets.txt"}'),
                target_scope="workspace:notes",
            ))
            # A mismatched attempt must not consume a valid grant.
            self.assertTrue(store.consume(grant, action, target_scope="workspace:notes"))
            self.assertFalse(store.consume(grant, action, target_scope="workspace:notes"))

    def test_approval_binds_scope_implementation_owner_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            action = self.make_action()
            grant = store.issue(action, target_scope="workspace:notes")
            self.assertFalse(store.consume(grant, action, target_scope="workspace:other"))
            self.assertFalse(store.consume(
                grant, self.make_action(handler=handler_b),
                target_scope="workspace:notes",
            ))
            self.assertFalse(store.consume(
                grant, self.make_action(owner="intruder"),
                target_scope="workspace:notes",
            ))
            self.assertFalse(store.consume(
                grant, self.make_action(session="session-b"),
                target_scope="workspace:notes",
            ))
            self.assertTrue(store.consume(grant, action, target_scope="workspace:notes"))

    def test_expired_grant_is_rejected_and_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            action = self.make_action()
            grant = store.issue(action, target_scope="workspace:notes")
            with store._connect() as db:
                db.execute(
                    "UPDATE tool_action_approvals SET expires_at = ? WHERE approval_id = ?",
                    ("2000-01-01T00:00:00+00:00", grant),
                )
            self.assertFalse(store.consume(grant, action, target_scope="workspace:notes"))
            with store._connect() as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM tool_action_approvals WHERE approval_id = ?", (grant,)).fetchone()[0],
                    0,
                )

    def test_concurrent_consumption_succeeds_at_most_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            action = self.make_action()
            grant = store.issue(action, target_scope="workspace:notes")
            barrier = threading.Barrier(3)
            results = []
            lock = threading.Lock()

            def consume():
                barrier.wait()
                result = store.consume(grant, action, target_scope="workspace:notes")
                with lock:
                    results.append(result)

            threads = [threading.Thread(target=consume) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 1)

    def test_capacity_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, max_pending=1)
            action = self.make_action()
            store.issue(action, target_scope="workspace:notes")
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                store.issue(action, target_scope="workspace:notes")

    def test_digest_is_versioned_and_rejects_missing_trusted_identity(self):
        action = self.make_action()
        self.assertEqual(len(action_approval_digest(action, "workspace:notes")), 64)
        missing_identity = self.make_action(owner=None)
        with self.assertRaisesRegex(ValueError, "trusted owner"):
            action_approval_digest(missing_identity, "workspace:notes")
        with self.assertRaises(ValueError):
            action_approval_digest(action, "")


if __name__ == "__main__":
    unittest.main()
