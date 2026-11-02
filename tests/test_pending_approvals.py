"""Regression tests for durable, identity-bound pending approvals."""
import multiprocessing
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from core.pending_approvals import SQLitePendingApprovalStore




def _claim_in_child_process(database_path, pending_id, barrier, results):
    store = SQLitePendingApprovalStore(database_path)
    barrier.wait(timeout=15)
    claimed = store.claim(
        pending_id, actor="owner", session_id="session-a"
    ) is not None
    results.put(claimed)


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

    def test_claim_retains_snapshot_and_prevents_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {"text": "recover me"},
                {"skill_id": "notes.write", "arguments": {"text": "recover me"}},
                actor="owner", session_id="session-a",
            )
            claimed = store.claim(item.pending_id, actor="owner", session_id="session-a")
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.status, "claimed")
            self.assertEqual(claimed.action["arguments"]["text"], "recover me")
            self.assertEqual(store.get_status(item.pending_id), "claimed")
            self.assertIsNone(store.claim(item.pending_id, actor="owner", session_id="session-a"))

    def test_lifecycle_transitions_are_compare_and_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            self.assertIsNotNone(store.claim(item.pending_id, actor="owner", session_id="session-a"))
            self.assertTrue(store.transition(item.pending_id, expected="claimed", new_status="approved"))
            self.assertFalse(store.transition(item.pending_id, expected="claimed", new_status="denied"))
            self.assertTrue(store.transition(item.pending_id, expected="approved", new_status="executing"))
            self.assertTrue(store.transition(item.pending_id, expected="executing", new_status="recovery_required"))
            self.assertEqual(store.get_status(item.pending_id), "recovery_required")
            with self.assertRaises(ValueError):
                store.transition(item.pending_id, expected="recovery_required", new_status="pending")

    def test_claim_is_atomic_across_independent_store_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pending.sqlite3")
            first = SQLitePendingApprovalStore(path)
            second = SQLitePendingApprovalStore(path)
            item = first.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            barrier = threading.Barrier(3)
            results = []
            lock = threading.Lock()

            def claim(store):
                barrier.wait()
                result = store.claim(item.pending_id, actor="owner", session_id="session-a")
                with lock:
                    results.append(result is not None)

            workers = [
                threading.Thread(target=claim, args=(first,)),
                threading.Thread(target=claim, args=(second,)),
            ]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(timeout=5)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 1)
            self.assertEqual(first.get_status(item.pending_id), "claimed")

    @unittest.skipUnless("spawn" in multiprocessing.get_all_start_methods(),
                         "requires multiprocessing spawn support")
    def test_claim_is_atomic_across_independent_processes(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp:
            database_path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(database_path)
            item = store.put(
                "notes.write", {},
                {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            barrier = context.Barrier(3)
            results = context.Queue()
            workers = [
                context.Process(
                    target=_claim_in_child_process,
                    args=(database_path, item.pending_id, barrier, results),
                )
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            try:
                barrier.wait(timeout=15)
                for worker in workers:
                    worker.join(timeout=20)
                self.assertTrue(all(not worker.is_alive() for worker in workers))
                self.assertTrue(all(worker.exitcode == 0 for worker in workers))
                outcomes = [results.get(timeout=5), results.get(timeout=5)]
                self.assertEqual(outcomes.count(True), 1)
                self.assertEqual(outcomes.count(False), 1)
                self.assertEqual(store.get_status(item.pending_id), "claimed")
            finally:
                for worker in workers:
                    if worker.is_alive():
                        worker.terminate()
                        worker.join(timeout=5)
                results.close()
                results.join_thread()

    def test_rejects_oversized_or_non_json_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            with self.assertRaises(ValueError):
                store.put("skill", {"x": float("nan")}, {}, actor="owner", session_id="s")
            with self.assertRaises(ValueError):
                store.put("skill", {"x": "a" * (17 * 1024)}, {}, actor="owner", session_id="s")

    @unittest.skipUnless(os.name == "posix", "POSIX file mode semantics")
    def test_pending_database_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.sqlite3"
            store = SQLitePendingApprovalStore(str(path))
            store.put("notes.write", {}, {}, actor="owner", session_id="session-a")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_recovery_items_are_listed_and_not_expired_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, ttl_seconds=1)
            item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            store.claim(item.pending_id, actor="owner", session_id="session-a")
            self.assertTrue(store.transition(item.pending_id, expected="claimed", new_status="recovery_required"))
            with store._connect() as db:
                rows = store.list_recovery_required()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "recovery_required")
            self.assertEqual(store.get_status(item.pending_id), "recovery_required")

    def test_expired_recovery_snapshot_remains_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            store.claim(item.pending_id, actor="owner", session_id="session-a")
            store.transition(item.pending_id, expected="claimed", new_status="recovery_required")
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET expires_at = '2000-01-01T00:00:00+00:00' WHERE pending_id = ?",
                    (item.pending_id,),
                )
            recovered = store.get(item.pending_id)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.status, "recovery_required")

    def test_capacity_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, max_pending=1)
            store.put("skill", {}, {}, actor="owner", session_id="s")
            with self.assertRaises(RuntimeError):
                store.put("skill", {}, {}, actor="owner", session_id="s")


if __name__ == "__main__":
    unittest.main()
