from contextlib import closing
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.reviewer_leases import (
    ReviewerLeaseAuthority, ReviewerLeaseConflict,
)


class ReviewerLeaseAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "leases.sqlite"
        self.allowed = True
        self.authority = ReviewerLeaseAuthority(
            self.path, secret=b"k" * 32,
            authorize=lambda actor, action, digest: self.allowed and digest == "a" * 64,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_acquire_validate_and_renew_fences_old_token(self):
        lease = self.authority.acquire(
            actor_id="supervisor", task_id="task-1", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1",
        )
        self.assertTrue(self.authority.validate(
            lease.token, task_id="task-1", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1", generation=1,
        ))
        renewed = self.authority.renew(actor_id="supervisor", token=lease.token)
        self.assertFalse(self.authority.validate(
            lease.token, task_id="task-1", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1", generation=1,
        ))
        self.assertTrue(self.authority.validate(
            renewed.token, task_id="task-1", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1", generation=1,
        ))

    def test_expired_lease_recovery_increments_generation_and_changes_reviewer(self):
        lease = self.authority.acquire(
            actor_id="supervisor", task_id="task-2", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1",
        )
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE reviewer_leases SET expires_at=? WHERE task_id=?",
                       (expired, "task-2"))
        recovered = self.authority.recover(
            actor_id="supervisor", task_id="task-2", artifact_sha256="a" * 64,
            replacement_reviewer_agent_id="reviewer-2",
        )
        self.assertEqual(recovered.generation, lease.generation + 1)
        self.assertFalse(self.authority.validate(
            lease.token, task_id="task-2", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1", generation=1,
        ))
        self.assertTrue(self.authority.validate(
            recovered.token, task_id="task-2", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-2", generation=2,
        ))

    def test_active_lease_cannot_be_reassigned_and_authorization_fails_closed(self):
        self.authority.acquire(
            actor_id="supervisor", task_id="task-3", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1",
        )
        with self.assertRaises(ReviewerLeaseConflict):
            self.authority.recover(
                actor_id="supervisor", task_id="task-3", artifact_sha256="a" * 64,
                replacement_reviewer_agent_id="reviewer-2",
            )
        self.allowed = False
        with self.assertRaises(PermissionError):
            self.authority.acquire(
                actor_id="supervisor", task_id="task-4", artifact_sha256="a" * 64,
                reviewer_agent_id="reviewer-1",
            )

    def test_history_requires_explicit_audit_permission(self):
        self.authority.acquire(
            actor_id="supervisor", task_id="task-5", artifact_sha256="a" * 64,
            reviewer_agent_id="reviewer-1",
        )
        events = self.authority.history(
            actor_id="auditor", task_id="task-5", artifact_sha256="a" * 64,
        )
        self.assertEqual(events[0]["event_type"], "acquired")


if __name__ == "__main__":
    unittest.main()
