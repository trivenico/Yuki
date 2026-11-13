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

    def test_idempotency_returns_same_owner_session_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            action = {"skill_id": "notes.write", "arguments": {"text": "hello"}}
            first = store.put(
                "notes.write", {"text": "hello"}, action,
                actor="owner", session_id="session-a", idempotency_key="conversation:request",
            )
            retry = store.put(
                "notes.write", {"text": "hello"}, action,
                actor="owner", session_id="session-a", idempotency_key="conversation:request",
            )
            self.assertTrue(first.created)
            self.assertFalse(retry.created)
            self.assertEqual(first.pending_id, retry.pending_id)

    def test_idempotency_rejects_changed_action_and_isolates_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            first = store.put(
                "notes.write", {"text": "hello"},
                {"skill_id": "notes.write", "arguments": {"text": "hello"}},
                actor="owner", session_id="session-a", idempotency_key="request-1",
            )
            with self.assertRaisesRegex(ValueError, "different action"):
                store.put(
                    "notes.write", {"text": "changed"},
                    {"skill_id": "notes.write", "arguments": {"text": "changed"}},
                    actor="owner", session_id="session-a", idempotency_key="request-1",
                )
            other_session = store.put(
                "notes.write", {"text": "hello"},
                {"skill_id": "notes.write", "arguments": {"text": "hello"}},
                actor="owner", session_id="session-b", idempotency_key="request-1",
            )
            self.assertNotEqual(first.pending_id, other_session.pending_id)

    def test_pending_list_is_scoped_to_owner_session_and_unexpired_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            first = store.put(
                "notes.write", {"text": "one"},
                {"skill_id": "notes.write", "arguments": {"text": "one"}},
                actor="owner", session_id="session-a",
            )
            store.put(
                "notes.write", {"text": "two"},
                {"skill_id": "notes.write", "arguments": {"text": "two"}},
                actor="owner", session_id="session-b",
            )
            store.put(
                "notes.write", {"text": "three"},
                {"skill_id": "notes.write", "arguments": {"text": "three"}},
                actor="other", session_id="session-a",
            )
            listed = store.list_pending_for_identity(actor="owner", session_id="session-a")
            self.assertEqual([item.pending_id for item in listed], [first.pending_id])
            self.assertEqual(store.list_pending_for_identity(
                actor="owner", session_id="session-b",
            )[0].arguments["text"], "two")
            self.assertEqual(store.list_pending_for_identity(
                actor="other", session_id="session-a",
            )[0].arguments["text"], "three")

    def test_capacity_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, max_pending=1)
            store.put("skill", {}, {}, actor="owner", session_id="s")
            with self.assertRaises(RuntimeError):
                store.put("skill", {}, {}, actor="owner", session_id="s")


if __name__ == "__main__":
    unittest.main()
