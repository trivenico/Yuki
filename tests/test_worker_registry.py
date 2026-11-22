import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.worker_registry import (
    WorkerRegistry, WorkerSessionError,
)


class WorkerRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = str(Path(self.tmp.name) / "workers.sqlite3")
        self.registry = WorkerRegistry(self.database)

    def test_register_and_heartbeat_persist_only_session_digest(self):
        session = self.registry.register(
            "worker-a", metadata={"role": "research", "version": "1.2"},
        )
        self.assertEqual(session.worker_id, "worker-a")
        self.assertTrue(session.session_token)
        self.registry.heartbeat("worker-a", session_token=session.session_token)
        records = self.registry.list_workers()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "active")
        self.assertEqual(records[0].metadata["role"], "research")
        with sqlite3.connect(self.database) as db:
            stored = db.execute(
                "SELECT session_hash, metadata_json FROM automation_workers"
            ).fetchone()
        self.assertNotEqual(stored[0], session.session_token)
        self.assertNotIn(session.session_token, stored[1])

    def test_child_heartbeat_requires_current_parent_session_atomically(self):
        parent = self.registry.register("parent-worker")
        child = self.registry.register(
            "child-worker",
            metadata={"parent_worker_id": parent.worker_id},
        )
        self.registry.heartbeat_child(
            child.worker_id, session_token=child.session_token,
            parent_worker_id=parent.worker_id,
            parent_session_token=parent.session_token,
        )
        before = next(
            item for item in self.registry.list_workers(limit=10)
            if item.worker_id == child.worker_id
        ).last_seen_at
        self.registry.register("parent-worker", metadata={"generation": 2})
        with self.assertRaises(WorkerSessionError):
            self.registry.heartbeat_child(
                child.worker_id, session_token=child.session_token,
                parent_worker_id=parent.worker_id,
                parent_session_token=parent.session_token,
            )
        after = next(
            item for item in self.registry.list_workers(limit=10)
            if item.worker_id == child.worker_id
        ).last_seen_at
        self.assertEqual(after, before)

    def test_re_registration_fences_old_process_session(self):
        old = self.registry.register("worker-a")
        new = self.registry.register("worker-a", metadata={"generation": 2})
        with self.assertRaises(WorkerSessionError):
            self.registry.heartbeat("worker-a", session_token=old.session_token)
        self.registry.heartbeat("worker-a", session_token=new.session_token)
        self.assertEqual(self.registry.list_workers()[0].metadata, {"generation": 2})

    def test_stopping_worker_can_heartbeat_until_stopped(self):
        session = self.registry.register("worker-a")
        stopping = self.registry.set_status(
            "worker-a", session_token=session.session_token, status="stopping",
        )
        self.assertEqual(stopping.status, "stopping")
        self.registry.heartbeat("worker-a", session_token=session.session_token)
        stopped = self.registry.set_status(
            "worker-a", session_token=session.session_token, status="stopped",
        )
        self.assertEqual(stopped.status, "stopped")
        with self.assertRaises(WorkerSessionError):
            self.registry.heartbeat("worker-a", session_token=session.session_token)

    def test_stale_status_is_derived_without_mutating_record(self):
        session = self.registry.register("worker-a")
        with sqlite3.connect(self.database) as db:
            db.execute(
                "UPDATE automation_workers SET last_seen_at='2000-01-01T00:00:00+00:00'"
            )
        self.assertEqual(self.registry.list_workers(stale_after_seconds=5)[0].status, "stale")
        self.assertEqual(self.registry.list_workers(stale_after_seconds=5)[0].status, "stale")
        self.registry.heartbeat("worker-a", session_token=session.session_token)
        self.assertEqual(self.registry.list_workers(stale_after_seconds=5)[0].status, "active")

    def test_child_session_is_fenced_when_parent_heartbeat_is_stale(self):
        parent = self.registry.register("parent-worker")
        child = self.registry.register(
            "child-worker",
            metadata={
                "role": "specialist_pool_child",
                "parent_worker_id": parent.worker_id,
                "parent_session_hash": __import__("hashlib").sha256(
                    parent.session_token.encode("utf-8")
                ).hexdigest(),
            },
        )
        with sqlite3.connect(self.database) as db:
            db.execute(
                "UPDATE automation_workers SET last_seen_at='2000-01-01T00:00:00+00:00' "
                "WHERE worker_id=?",
                (parent.worker_id,),
            )
        child_before = next(
            item for item in self.registry.list_workers(limit=10)
            if item.worker_id == child.worker_id
        ).last_seen_at
        with self.assertRaises(WorkerSessionError):
            self.registry.heartbeat_child(
                child.worker_id, session_token=child.session_token,
                parent_worker_id=parent.worker_id,
                parent_session_token=parent.session_token,
            )
        with self.assertRaises(WorkerSessionError):
            self.registry.assert_current_session(
                child.worker_id, session_token=child.session_token,
            )
        child_after = next(
            item for item in self.registry.list_workers(limit=10)
            if item.worker_id == child.worker_id
        ).last_seen_at
        self.assertEqual(child_after, child_before)

    def test_metadata_and_limits_are_validated(self):
        with self.assertRaises(ValueError):
            self.registry.register(" ")
        with self.assertRaises(ValueError):
            self.registry.register("worker-a", metadata={"bad": object()})
        with self.assertRaises(ValueError):
            self.registry.register("worker-a", metadata={"payload": "x" * 5000})
        with self.assertRaises(ValueError):
            self.registry.list_workers(limit=1001)
        with self.assertRaises(ValueError):
            self.registry.list_workers(stale_after_seconds=0)

    def test_session_operations_fail_closed_for_unknown_or_stale_tokens(self):
        with self.assertRaises(WorkerSessionError):
            self.registry.heartbeat("worker-missing", session_token="unknown")
        session = self.registry.register("worker-a")
        with self.assertRaises(WorkerSessionError):
            self.registry.set_status(
                "worker-a", session_token="wrong-session", status="stopped",
            )
        self.registry.set_status(
            "worker-a", session_token=session.session_token, status="stopped",
        )
        with self.assertRaises(WorkerSessionError):
            self.registry.set_status(
                "worker-a", session_token=session.session_token, status="stopping",
            )

    def test_assert_current_session_rejects_replaced_session(self):
        old = self.registry.register("worker-a")
        self.registry.register("worker-a")
        with self.assertRaises(WorkerSessionError):
            self.registry.assert_current_session(
                "worker-a", session_token=old.session_token,
            )

    def test_assert_current_session_rejects_stopping_and_stopped(self):
        session = self.registry.register("worker-a")
        self.registry.set_status(
            "worker-a", session_token=session.session_token, status="stopping",
        )
        with self.assertRaises(WorkerSessionError):
            self.registry.assert_current_session(
                "worker-a", session_token=session.session_token,
            )


if __name__ == "__main__":
    unittest.main()
