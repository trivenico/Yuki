"""Tests for the authorized incident analysis adapter."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.reviewer_incident_analysis import analyze_recovery_incidents
from automation.reviewer_recovery_incidents import ReviewerRecoveryIncidentStore


class ReviewerIncidentAnalysisAdapterTests(unittest.TestCase):
    def test_adapter_uses_authorized_store_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReviewerRecoveryIncidentStore(
                Path(tmp) / "incidents.sqlite",
                authorize=lambda actor, action, task: actor == "operator",
            )
            store.begin_attempt(
                actor_id="operator", task_id="task-1", worker_id="worker-1",
            )
            report = analyze_recovery_incidents(store, actor_id="operator")
            self.assertEqual(report.incidents_analyzed, 1)
            self.assertEqual(report.findings[0].code, "unclassified")

    def test_adapter_cannot_bypass_store_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReviewerRecoveryIncidentStore(
                Path(tmp) / "incidents.sqlite",
                authorize=lambda *_: False,
            )
            with self.assertRaises(PermissionError):
                analyze_recovery_incidents(store, actor_id="intruder")


if __name__ == "__main__":
    unittest.main()
