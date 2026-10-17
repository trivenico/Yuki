import tempfile
import unittest
from pathlib import Path

from core.action_approvals import SQLiteActionApprovalStore


class SQLiteActionApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "approvals.sqlite3")
        self.store = SQLiteActionApprovalStore(self.path, ttl_seconds=60)
        self.action = {"skill_id": "notes.write", "arguments": {"text": "hello"}}

    def test_token_survives_store_recreation_and_consumes_once(self):
        token = self.store.issue(self.action, actor="owner", session_id="session-1", approved=True)
        restarted_store = SQLiteActionApprovalStore(self.path, ttl_seconds=60)
        self.assertTrue(restarted_store.consume(token.token, self.action, actor="owner", session_id="session-1"))
        self.assertFalse(restarted_store.consume(token.token, self.action, actor="owner", session_id="session-1"))

    def test_token_hash_not_raw_token_is_persisted(self):
        token = self.store.issue(self.action, actor="owner", session_id="session-1", approved=True)
        import sqlite3
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT token_hash FROM action_approval_tokens").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][0], token.token)

    def test_changed_action_or_identity_is_denied(self):
        token = self.store.issue(self.action, actor="owner", session_id="session-1", approved=True)
        changed = {"skill_id": "notes.write", "arguments": {"text": "different"}}
        self.assertFalse(self.store.consume(token.token, changed, actor="owner", session_id="session-1"))
        token = self.store.issue(self.action, actor="owner", session_id="session-1", approved=True)
        self.assertFalse(self.store.consume(token.token, self.action, actor="other", session_id="session-1"))

    def test_unapproved_issue_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.store.issue(self.action, actor="owner", session_id="session-1", approved=False)


if __name__ == "__main__":
    unittest.main()
