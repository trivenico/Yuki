"""Read-only recovery control plane tests."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.workflow_store import WorkflowRunStore
from providers.durable_provider_governance import SQLiteProviderGovernanceStore
from providers.recovery_control_plane import YukiRecoveryControlPlane


class RecoveryControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.provider = SQLiteProviderGovernanceStore(str(Path(self.tmp.name) / "provider.db"))
        self.workflow = WorkflowRunStore(str(Path(self.tmp.name) / "workflow.db"))
        self.control = YukiRecoveryControlPlane(
            workflow_store=self.workflow, provider_store=self.provider,
        )

    def test_empty_snapshot_is_read_only_and_reports_correlation_gap(self):
        snapshot = self.control.snapshot(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertTrue(snapshot["read_only"])
        self.assertFalse(snapshot["automatic_retry_permitted"])
        self.assertEqual(snapshot["correlation"]["status"], "not_implemented")
        self.assertEqual(snapshot["health"]["status"], "no_reported_recovery_items")

    def test_provider_uncertain_execution_is_visible_and_stale(self):
        lease = self.provider.acquire(provider_id="localengine", agent_id="assistant")
        self.provider._clock = lambda: 1000.0
        self.provider.snapshot(provider_id="localengine", agent_id="assistant")
        snapshot = self.control.snapshot(
            now=datetime.fromtimestamp(2000, tz=timezone.utc),
            stale_after_seconds=300,
        )
        self.assertEqual(snapshot["provider"]["uncertain"], 1)
        self.assertEqual(snapshot["provider"]["items"][0]["execution_id"], lease.execution_id)
        self.assertTrue(snapshot["provider"]["items"][0]["stale"])
        self.assertEqual(snapshot["correlation"]["matched"], 0)

    def test_limits_are_validated(self):
        with self.assertRaises(ValueError):
            self.control.snapshot(limit=0)
        with self.assertRaises(ValueError):
            self.control.snapshot(stale_after_seconds=0)


if __name__ == "__main__":
    unittest.main()
