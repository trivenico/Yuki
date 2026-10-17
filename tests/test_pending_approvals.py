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
        pending = self.store.put("notes.write", self.arguments, self.action)
        restarted = SQLitePendingApprovalStore(self.path, ttl_seconds=60, max_pending=2)
        found = restarted.get(pending.pending_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.action, self.action)

    def test_consume_is_one_time(self):
        pending = self.store.put("notes.write", self.arguments, self.action)
        self.assertIsNotNone(self.store.consume(pending.pending_id))
        self.assertIsNone(self.store.consume(pending.pending_id))

    def test_deny_removes_pending(self):
        pending = self.store.put("notes.write", self.arguments, self.action)
        self.assertTrue(self.store.deny(pending.pending_id))
        self.assertFalse(self.store.deny(pending.pending_id))

    def test_capacity_is_bounded(self):
        self.store.put("notes.write", self.arguments, self.action)
        self.store.put("notes.write", self.arguments, self.action)
        with self.assertRaises(RuntimeError):
            self.store.put("notes.write", self.arguments, self.action)

    def test_oversized_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            self.store.put("notes.write", {"content": "x" * 17000}, self.action)


if __name__ == "__main__":
    unittest.main()
