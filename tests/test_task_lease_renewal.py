import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore, WorkflowTransitionError


class TaskLeaseRenewalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "leases.sqlite3"))
        self.grants = {"task:create", "task:read", "task:lease:renew"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "worker-actor" and action in self.grants,
        )
        task = self.runtime.enqueue(
            actor_id="worker-actor", idempotency_key="lease-test",
            name="long-agent-task",
            steps=[{"name": "work", "action": "emit", "argument": "bounded",
                    "requires_approval": False}],
        )
        self.claimed = self.store.claim_next(worker_id="worker-1")

    def renew(self, **overrides):
        args = {
            "actor_id": "worker-actor", "task_id": self.claimed.run_id,
            "worker_id": "worker-1", "expected_version": self.claimed.version,
            "lease_seconds": 120,
        }
        args.update(overrides)
        return self.runtime.renew_lease(**args)

    def test_current_worker_can_renew_with_version_increment(self):
        renewed = self.renew()
        self.assertEqual(renewed.state, "running")
        self.assertEqual(renewed.lease_owner, "worker-1")
        self.assertEqual(renewed.version, self.claimed.version + 1)
        self.assertGreater(renewed.lease_expires_at, self.claimed.lease_expires_at)

    def test_wrong_worker_is_rejected(self):
        with self.assertRaises(WorkflowTransitionError):
            self.renew(worker_id="worker-2")

    def test_stale_version_is_rejected(self):
        self.renew()
        with self.assertRaises(WorkflowConflict):
            self.renew()

    def test_expired_lease_cannot_be_resurrected(self):
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                ("2000-01-01T00:00:00+00:00", self.claimed.run_id),
            )
        with self.assertRaises(WorkflowConflict):
            self.renew()

    def test_authorization_fails_closed(self):
        restricted = TaskRuntime(self.store, authorize=lambda *_: False)
        with self.assertRaises(PermissionError):
            restricted.renew_lease(
                actor_id="worker-actor", task_id=self.claimed.run_id,
                worker_id="worker-1", expected_version=self.claimed.version,
            )

    def test_invalid_worker_or_version_is_rejected(self):
        with self.assertRaises(ValueError):
            self.renew(worker_id=" ")
        with self.assertRaises(ValueError):
            self.renew(expected_version=True)
        with self.assertRaises(ValueError):
            self.renew(lease_seconds=0)


if __name__ == "__main__":
    unittest.main()
