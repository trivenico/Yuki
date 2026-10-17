import tempfile
import unittest
from pathlib import Path

from core.pending_approvals import SQLitePendingApprovalStore


class SQLitePendingApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "pending.sqlite3")
        self.store = SQLitePendingApprovalStore(self.path, ttl_seconds=60, max_pending=2)
        self.arguments = {"path": "notes/today.txt", "content": "hello"}
        self.action = {"skill_id": "notes.write", "version": "1.0", "arguments": self.arguments,
                       "capabilities": ["workspace.write"]}

    def test_pending_action_survives_recreation(self):
        pending = self.store.put("notes.write", self.arguments, self.action, actor="owner", session_id="cli-session")
        restarted = SQLitePendingApprovalStore(self.path, ttl_seconds=60, max_pending=2)
        found = restarted.get(pending.pending_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.action, self.action)

    def test_consume_is_one_time(self):
        pending = self.store.put("notes.write", self.arguments, self.action, actor="owner", session_id="cli-session")
        self.assertIsNotNone(self.store.consume(pending.pending_id, actor="owner", session_id="cli-session"))
        self.assertIsNone(self.store.consume(pending.pending_id, actor="owner", session_id="cli-session"))

    def test_identity_hashes_persist_without_raw_actor_or_session(self):
        pending = self.store.put("notes.write", self.arguments, self.action, actor="owner-secret", session_id="session-secret")
        found = self.store.get(pending.pending_id)
        self.assertEqual(found.actor_hash, self.store._identity_hash("owner-secret"))
        self.assertEqual(found.session_hash, self.store._identity_hash("session-secret"))
        with self.store._connect() as db:
            raw = str(db.execute("SELECT * FROM pending_approvals").fetchall())
        self.assertNotIn("owner-secret", raw)
        self.assertNotIn("session-secret", raw)

    def test_wrong_identity_cannot_consume_pending_action(self):
        pending = self.store.put("notes.write", self.arguments, self.action, actor="owner", session_id="cli-session")
        self.assertIsNone(self.store.consume(pending.pending_id, actor="intruder", session_id="cli-session"))
        self.assertIsNotNone(self.store.get(pending.pending_id))
        self.assertIsNone(self.store.consume(pending.pending_id, actor="owner", session_id="other-session"))
        self.assertIsNotNone(self.store.consume(pending.pending_id, actor="owner", session_id="cli-session"))

    def test_deny_removes_pending(self):
        pending = self.store.put("notes.write", self.arguments, self.action, actor="owner", session_id="cli-session")
        self.assertTrue(self.store.deny(pending.pending_id))
        self.assertFalse(self.store.deny(pending.pending_id))

    def test_capacity_is_bounded(self):
        self.store.put("notes.write", self.arguments, self.action, actor="owner", session_id="cli-session")
        self.store.put("notes.write", self.arguments, self.action, actor="owner", session_id="cli-session")
        with self.assertRaises(RuntimeError):
            self.store.put("notes.write", self.arguments, self.action, actor="owner", session_id="cli-session")

    def test_oversized_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            self.store.put("notes.write", {"content": "x" * 17000}, self.action, actor="owner", session_id="cli-session")


if __name__ == "__main__":
    unittest.main()
