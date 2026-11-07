"""Tests for durable reviewer worker registration and global slots."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.reviewer_worker_registry import (
    ReviewerWorkerRegistry, ReviewerWorkerRegistryConflict,
)


class ReviewerWorkerRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "workers.sqlite3")
        self.registry = ReviewerWorkerRegistry(
            self.db, authorize=lambda actor, action, worker: actor == worker
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_heartbeat_and_stop(self):
        reg = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                     instance_id="instance-a", lease_seconds=60)
        expiry = self.registry.heartbeat(
            actor_id="reviewer-a", worker_id="reviewer-a",
            instance_id=reg.instance_id, lease_token=reg.lease_token,
        )
        self.assertGreater(expiry, reg.lease_expires_at)
        self.registry.stop(actor_id="reviewer-a", worker_id="reviewer-a",
                           instance_id=reg.instance_id, lease_token=reg.lease_token)
        self.assertEqual(self.registry.list_workers(
            actor_id="reviewer-a", worker_id="reviewer-a")[0].state, "stopped")

    def test_global_concurrency_limit_is_shared_across_instances(self):
        first = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                       instance_id="instance-a")
        second = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                        instance_id="instance-b")
        self.registry.claim_slot(actor_id="reviewer-a", worker_id="reviewer-a",
                                 instance_id=first.instance_id, lease_token=first.lease_token,
                                 max_concurrency=1)
        with self.assertRaisesRegex(ReviewerWorkerRegistryConflict,
                                    "reviewer_global_concurrency_limit_reached"):
            self.registry.claim_slot(actor_id="reviewer-a", worker_id="reviewer-a",
                                     instance_id=second.instance_id, lease_token=second.lease_token,
                                     max_concurrency=1)

    def test_slot_renewal_is_bound_to_live_instance(self):
        reg = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                     instance_id="instance-a")
        slot = self.registry.claim_slot(
            actor_id="reviewer-a", worker_id="reviewer-a",
            instance_id=reg.instance_id, lease_token=reg.lease_token,
            max_concurrency=1, slot_seconds=30,
        )
        expiry = self.registry.renew_slot(
            actor_id="reviewer-a", worker_id="reviewer-a",
            instance_id=reg.instance_id, lease_token=reg.lease_token,
            slot_id=slot, slot_seconds=60,
        )
        self.assertLessEqual(expiry, reg.lease_expires_at)

    def test_instance_id_cannot_be_reassigned_to_another_worker(self):
        reg = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                     instance_id="instance-a", lease_seconds=10)
        # Expiry is simulated by making the lease timestamp stale in SQLite.
        import sqlite3
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE reviewer_worker_instances SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE instance_id=?",
                       (reg.instance_id,))
        with self.assertRaisesRegex(ReviewerWorkerRegistryConflict,
                                    "worker_instance_identity_conflict"):
            self.registry.register(actor_id="reviewer-b", worker_id="reviewer-b",
                                   instance_id="instance-a")

    def test_global_concurrency_policy_cannot_drift_between_processes(self):
        first = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                       instance_id="instance-a")
        second = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                        instance_id="instance-b")
        self.registry.claim_slot(actor_id="reviewer-a", worker_id="reviewer-a",
                                 instance_id=first.instance_id, lease_token=first.lease_token,
                                 max_concurrency=2)
        with self.assertRaisesRegex(ReviewerWorkerRegistryConflict,
                                    "reviewer_global_concurrency_configuration_mismatch"):
            self.registry.claim_slot(actor_id="reviewer-a", worker_id="reviewer-a",
                                     instance_id=second.instance_id, lease_token=second.lease_token,
                                     max_concurrency=3)

    def test_expired_or_wrong_worker_token_cannot_claim_slot(self):
        reg = self.registry.register(actor_id="reviewer-a", worker_id="reviewer-a",
                                     instance_id="instance-a")
        with self.assertRaisesRegex(ReviewerWorkerRegistryConflict, "worker_lease_invalid"):
            self.registry.claim_slot(actor_id="reviewer-a", worker_id="reviewer-a",
                                     instance_id=reg.instance_id, lease_token="z" * 32,
                                     max_concurrency=1)

    def test_authorization_fails_closed(self):
        denied = ReviewerWorkerRegistry(self.db, authorize=lambda *_: False)
        with self.assertRaises(PermissionError):
            denied.register(actor_id="reviewer-a", worker_id="reviewer-a")


if __name__ == "__main__":
    unittest.main()
