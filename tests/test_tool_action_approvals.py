"""Regression tests for durable exact-action tool approvals."""
import tempfile
import hashlib
import sqlite3
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
                    ("2000-01-01T00:00:00+00:00", hashlib.sha256(grant.encode("utf-8")).hexdigest()),
                )
            self.assertFalse(store.consume(grant, action, target_scope="workspace:notes"))
            with store._connect() as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM tool_action_approvals WHERE approval_id = ?", (hashlib.sha256(grant.encode("utf-8")).hexdigest(),)).fetchone()[0],
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

    def test_bearer_approval_token_is_not_persisted_in_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approvals.sqlite3"
            store = SQLiteToolApprovalStore(str(path))
            action = self.make_action()
            grant = store.issue(action, target_scope="workspace:notes")
            with open(path, "rb") as handle:
                database_bytes = handle.read()
            self.assertNotIn(grant.encode("ascii"), database_bytes)
            self.assertTrue(store.consume(grant, action, target_scope="workspace:notes"))

    def test_initialization_revokes_legacy_plaintext_bearer_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approvals.sqlite3"
            legacy_token = "legacy-plaintext-bearer-token"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE tool_action_approvals (
                    approval_id TEXT PRIMARY KEY,
                    action_digest TEXT NOT NULL,
                    actor_hash TEXT NOT NULL,
                    session_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )""")
                db.execute(
                    "INSERT INTO tool_action_approvals VALUES (?, ?, ?, ?, ?)",
                    (legacy_token, "a" * 64, "b" * 64, "c" * 64, "2999-01-01T00:00:00+00:00"),
                )

            SQLiteToolApprovalStore(str(path))
            with sqlite3.connect(path) as db:
                rows = db.execute("SELECT approval_id FROM tool_action_approvals").fetchall()
            self.assertEqual(rows, [])

    def test_capacity_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, max_pending=1)
            action = self.make_action()
            store.issue(action, target_scope="workspace:notes")
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                store.issue(action, target_scope="workspace:notes")

    def test_code_fingerprint_does_not_attest_mutable_closure_state(self):
        from core.tool_action import implementation_identity

        def make_handler(value):
            def handler(argument):
                return value + argument
            return handler

        first = make_handler("approved:")
        changed_state = make_handler("unapproved:")
        # Characterizes a known limitation: code-object fingerprints are
        # identical although captured behavior differs. Such closures must not
        # be registered as consequential production tools.
        self.assertEqual(implementation_identity(first), implementation_identity(changed_state))

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
