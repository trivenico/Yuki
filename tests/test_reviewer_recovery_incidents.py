"""Tests for durable recovery incident attempt limits and audit events."""
from __future__ import annotations
from contextlib import closing

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
        with closing(sqlite3.connect(self.store.database)) as db, db:
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

    def test_dead_letter_requires_authorized_operator_remediation(self):
        self.store.begin_attempt(
            actor_id="recovery-controller", task_id="task-c",
            worker_id="reviewer-a",
        )
        self.store.record_outcome(
            actor_id="recovery-controller", task_id="task-c",
            state="dead_letter", reason_code="repeated_failure",
        )
        with self.assertRaises(PermissionError):
            self.store.remediate_dead_letter(
                actor_id="intruder", task_id="task-c",
                reason_code="operator_reviewed",
            )
        repaired = self.store.remediate_dead_letter(
            actor_id="recovery-controller", task_id="task-c",
            reason_code="operator_reviewed",
        )
        self.assertEqual(repaired.state, "open")
        self.assertEqual(repaired.attempts, 0)
        self.assertEqual(repaired.max_attempts, 2)
        events = self.store.list_events(
            actor_id="recovery-controller", task_id="task-c",
        )
        self.assertEqual(events[-1]["event_type"], "operator_remediated")

    def test_incident_listing_is_authorized_and_bounded(self):
        self.store.begin_attempt(
            actor_id="recovery-controller", task_id="task-d",
            worker_id="reviewer-a",
        )
        rows = self.store.list_incidents(
            actor_id="recovery-controller", state="open", limit=10,
        )
        self.assertEqual([row.task_id for row in rows], ["task-d"])
        with self.assertRaises(PermissionError):
            self.store.list_incidents(actor_id="intruder")

    def test_authorization_is_required_for_inspection(self):
        with self.assertRaises(PermissionError):
            self.store.list_events(actor_id="intruder", task_id="task-a")


if __name__ == "__main__":
    unittest.main()
