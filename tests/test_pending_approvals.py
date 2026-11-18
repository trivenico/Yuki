"""Regression tests for durable, identity-bound pending approvals."""
import tempfile
import threading
import unittest
from pathlib import Path

from core.pending_approvals import SQLitePendingApprovalStore


class SQLitePendingApprovalStoreTests(unittest.TestCase):
    def make_store(self, directory, **kwargs):
        return SQLitePendingApprovalStore(str(Path(directory) / "pending.sqlite3"), **kwargs)

    def test_list_pending_exposes_metadata_without_action_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "private action argument"},
                {"skill_id": "notes.write", "arguments": {"text": "private action argument"}},
                actor="owner", session_id="session-a",
            )
            rows = store.list_pending(actor="owner", session_id="session-a")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pending_id"], item.pending_id)
            self.assertEqual(rows[0]["skill_id"], "notes.write")
            self.assertIn("expires_at", rows[0])
            self.assertNotIn("arguments", rows[0])
            self.assertNotIn("action", rows[0])
            self.assertEqual(store.list_pending(actor="another-user", session_id="session-a"), [])
            self.assertEqual(store.list_pending(actor="owner", session_id="session-b"), [])
            with self.assertRaises(ValueError):
                store.list_pending(actor="owner", session_id="")
            for limit in (0, 201, True):
                with self.assertRaises(ValueError):
                    store.list_pending(actor="owner", session_id="session-a", limit=limit)

    def test_consume_is_bound_to_actor_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "hello"},
                {"skill_id": "notes.write", "arguments": {"text": "hello"}},
                actor="owner", session_id="session-a",
            )
            self.assertIsNone(store.consume(item.pending_id, actor="other", session_id="session-a"))
            self.assertIsNone(store.consume(item.pending_id, actor="owner", session_id="session-b"))
            consumed = store.consume(item.pending_id, actor="owner", session_id="session-a")
            self.assertIsNotNone(consumed)
            self.assertIsNone(store.consume(item.pending_id, actor="owner", session_id="session-a"))

    def test_claim_is_atomic_across_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "hello"},
                {"skill_id": "notes.write", "arguments": {"text": "hello"}},
                actor="owner", session_id="session-a",
            )
            barrier = threading.Barrier(3)
            outcomes = []
            lock = threading.Lock()

            def claim():
                barrier.wait()
                result = store.consume(item.pending_id, actor="owner", session_id="session-a")
                with lock:
                    outcomes.append(result is not None)

            workers = [threading.Thread(target=claim) for _ in range(2)]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(timeout=5)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 1)

    def test_rejects_oversized_or_non_json_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            with self.assertRaises(ValueError):
                store.put("skill", {"x": float("nan")}, {}, actor="owner", session_id="s")
            with self.assertRaises(ValueError):
                store.put("skill", {"x": "a" * (17 * 1024)}, {}, actor="owner", session_id="s")

    def test_capacity_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, max_pending=1)
            store.put("skill", {}, {}, actor="owner", session_id="s")
            with self.assertRaises(RuntimeError):
                store.put("skill", {}, {}, actor="owner", session_id="s")


if __name__ == "__main__":
    unittest.main()
