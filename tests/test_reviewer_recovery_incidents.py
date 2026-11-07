"""Tests for durable recovery incident attempt limits and audit events."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.reviewer_recovery_incidents import ReviewerRecoveryIncidentStore


class ReviewerRecoveryIncidentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReviewerRecoveryIncidentStore(
            Path(self.tmp.name) / "incidents.sqlite",
            authorize=lambda actor, action, task: actor == "recovery-controller",
            default_max_attempts=2, default_cooldown_seconds=60,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_attempt_limit_transitions_to_dead_letter(self):
        self.assertTrue(self.store.begin_attempt(
            actor_id="recovery-controller", task_id="task-a", worker_id="reviewer-a",
        ))
        first = self.store.record_outcome(
            actor_id="recovery-controller", task_id="task-a",
            state="cooldown", reason_code="replacement_unavailable",
        )
        self.assertEqual(first.state, "cooldown")
        # Expire cooldown directly to make this deterministic without sleeping.
        import sqlite3
        with sqlite3.connect(self.store.database) as db:
            db.execute("UPDATE reviewer_recovery_incidents SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE task_id='task-a'")
        self.assertTrue(self.store.begin_attempt(
            actor_id="recovery-controller", task_id="task-a", worker_id="reviewer-a",
        ))
        second = self.store.record_outcome(
            actor_id="recovery-controller", task_id="task-a",
            state="cooldown", reason_code="replacement_unavailable",
        )
        self.assertEqual(second.state, "dead_letter")
        self.assertFalse(self.store.begin_attempt(
            actor_id="recovery-controller", task_id="task-a", worker_id="reviewer-a",
        ))

    def test_recovered_incident_is_terminal(self):
        self.assertTrue(self.store.begin_attempt(
            actor_id="recovery-controller", task_id="task-b", worker_id="reviewer-a",
        ))
        self.store.record_outcome(
            actor_id="recovery-controller", task_id="task-b",
            state="recovered", reason_code="lease_reassigned",
        )
        self.assertFalse(self.store.begin_attempt(
            actor_id="recovery-controller", task_id="task-b", worker_id="reviewer-a",
        ))

    def test_authorization_is_required_for_inspection(self):
        with self.assertRaises(PermissionError):
            self.store.list_events(actor_id="intruder", task_id="task-a")


if __name__ == "__main__":
    unittest.main()
