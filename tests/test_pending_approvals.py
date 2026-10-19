import tempfile
import unittest
from pathlib import Path
import time

from core.pending_approvals import SQLitePendingApprovalStore, StoredPendingAction
from yuki_approvals import ApprovalGate, ApprovalRequest


class SQLitePendingApprovalStoreTests(unittest.TestCase):
    def test_put_get_and_consume_pending_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(db_path, ttl_seconds=300)
            stored = store.put("notes.write", {"text": "hello"}, {"type": "write"},
                               actor="owner", session_id="cli-1")
            self.assertIsInstance(stored, StoredPendingAction)
            self.assertEqual(stored.skill_id, "notes.write")
            self.assertEqual(stored.arguments, {"text": "hello"})

            fetched = store.get(stored.pending_id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.pending_id, stored.pending_id)

            consumed = store.consume(stored.pending_id, actor="owner", session_id="cli-1")
            self.assertIsNotNone(consumed)
            self.assertEqual(consumed.pending_id, stored.pending_id)

            # Consumed actions are deleted atomically (one-time claim)
            self.assertIsNone(store.get(stored.pending_id))
            self.assertIsNone(store.consume(stored.pending_id, actor="owner", session_id="cli-1"))

    def test_actor_and_session_binding_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(db_path)
            stored = store.put("notes.write", {"text": "bound"}, {"type": "write"},
                               actor="owner", session_id="cli-1")

            wrong_actor = store.consume(stored.pending_id, actor="attacker", session_id="cli-1")
            self.assertIsNone(wrong_actor)

            wrong_session = store.consume(stored.pending_id, actor="owner", session_id="cli-2")
            self.assertIsNone(wrong_session)

            valid = store.consume(stored.pending_id, actor="owner", session_id="cli-1")
            self.assertIsNotNone(valid)

    def test_deny_removes_pending_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(db_path)
            stored = store.put("notes.write", {"text": "deny_me"}, {"type": "write"},
                               actor="owner", session_id="cli-1")

            self.assertTrue(store.deny(stored.pending_id))
            self.assertFalse(store.deny(stored.pending_id))
            self.assertIsNone(store.get(stored.pending_id))

    def test_max_pending_capacity_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(db_path, max_pending=2)
            store.put("skill1", {}, {}, actor="owner", session_id="s1")
            store.put("skill2", {}, {}, actor="owner", session_id="s1")

            with self.assertRaises(RuntimeError):
                store.put("skill3", {}, {}, actor="owner", session_id="s1")

    def test_ttl_expiration(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(db_path, ttl_seconds=1)
            stored = store.put("skill1", {}, {}, actor="owner", session_id="s1")
            time.sleep(1.1)

            self.assertIsNone(store.get(stored.pending_id))
            self.assertIsNone(store.consume(stored.pending_id, actor="owner", session_id="s1"))


class ApprovalGateTests(unittest.TestCase):
    def test_approval_gate_request_and_decide(self):
        gate = ApprovalGate()
        req = gate.request("system_file_write", "Save notes to file")
        self.assertIsInstance(req, ApprovalRequest)
        self.assertEqual(req.status, "pending")
        self.assertEqual(len(gate.list_pending()), 1)

        result = gate.decide(req.id, approved=True)
        self.assertIn("approved", result)
        self.assertEqual(len(gate.list_pending()), 0)

    def test_approval_gate_rejects_invalid_inputs(self):
        gate = ApprovalGate()
        with self.assertRaises(ValueError):
            gate.request("", "Rationale")
        with self.assertRaises(ValueError):
            gate.request("Action", "")

        req = gate.request("ValidAction", "ValidRationale")
        with self.assertRaises(ValueError):
            gate.decide(req.id, approved="not_a_bool")


if __name__ == "__main__":
    unittest.main()
