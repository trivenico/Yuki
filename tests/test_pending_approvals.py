"""Regression tests for durable, identity-bound pending approvals."""
import tempfile
import threading
import unittest
from pathlib import Path

from core.pending_approvals import SQLitePendingApprovalStore


class SQLitePendingApprovalStoreTests(unittest.TestCase):
    def make_store(self, directory, **kwargs):
        return SQLitePendingApprovalStore(str(Path(directory) / "pending.sqlite3"), **kwargs)

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

    def test_put_rejects_mismatch_between_action_and_stored_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            with self.assertRaisesRegex(ValueError, "must match"):
                store.put(
                    "notes.write", {"text": "safe"},
                    {"skill_id": "shell.exec", "arguments": {"text": "tampered"}},
                    actor="owner", session_id="session-a",
                )
            with self.assertRaisesRegex(ValueError, "must match"):
                store.put(
                    "notes.write", {"text": "safe"},
                    {"skill_id": "notes.write", "arguments": {"text": "different"}},
                    actor="owner", session_id="session-a",
                )

    def test_modified_persisted_action_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "safe"},
                {"skill_id": "notes.write", "arguments": {"text": "safe"}},
                actor="owner", session_id="session-a",
            )
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET action_json = ? WHERE pending_id = ?",
                    ('{"skill_id":"notes.write","arguments":{"text":"tampered"}}', item.pending_id),
                )
            self.assertIsNone(
                store.consume(item.pending_id, actor="owner", session_id="session-a")
            )
            self.assertIsNone(store.get(item.pending_id))

    def test_modified_arguments_are_rejected_even_if_action_json_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "safe"},
                {"skill_id": "notes.write", "arguments": {"text": "safe"}},
                actor="owner", session_id="session-a",
            )
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET arguments_json = ? WHERE pending_id = ?",
                    ('{"text":"tampered"}', item.pending_id),
                )
            self.assertIsNone(store.consume(item.pending_id, actor="owner", session_id="session-a"))

    def test_modified_skill_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "safe"},
                {"skill_id": "notes.write", "arguments": {"text": "safe"}},
                actor="owner", session_id="session-a",
            )
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET skill_id = ? WHERE pending_id = ?",
                    ("shell.exec", item.pending_id),
                )
            self.assertIsNone(store.consume(item.pending_id, actor="owner", session_id="session-a"))

    def test_legacy_snapshot_without_action_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "safe"},
                {"skill_id": "notes.write", "arguments": {"text": "safe"}},
                actor="owner", session_id="session-a",
            )
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET action_hash = NULL WHERE pending_id = ?",
                    (item.pending_id,),
                )
            self.assertIsNone(store.consume(item.pending_id, actor="owner", session_id="session-a"))

    def test_capacity_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, max_pending=1)
            store.put("skill", {}, {"skill_id": "skill", "arguments": {}}, actor="owner", session_id="s")
            with self.assertRaises(RuntimeError):
                store.put("skill", {}, {}, actor="owner", session_id="s")


if __name__ == "__main__":
    unittest.main()
