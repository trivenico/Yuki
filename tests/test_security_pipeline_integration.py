import os
import tempfile
import unittest

from core.security_alert_outbox import SecurityAlertOutbox
from core.security_event_ingestion import SecurityEventIngestionCursors
from core.security_event_monitor import SecurityEventMonitor
from core.security_operations_health import SecurityOperationsHealth, SecurityWorkerHeartbeat


class SecurityPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.monitor = SecurityEventMonitor(os.path.join(self.tmp.name, "monitor.sqlite"),
            authorize_operator=lambda value: value == "ops", clock=lambda: self.now)
        self.outbox = SecurityAlertOutbox(os.path.join(self.tmp.name, "outbox.sqlite"),
            authorize_operator=lambda value: value == "ops", clock=lambda: self.now)
        self.heartbeats = SecurityWorkerHeartbeat(os.path.join(self.tmp.name, "heartbeats.sqlite"),
            clock=lambda: self.now)
        self.cursors = SecurityEventIngestionCursors(os.path.join(self.tmp.name, "cursors.sqlite"),
            clock=lambda: self.now)
        for role in self.heartbeats.ROLES:
            self.heartbeats.beat(role, "worker-1")
        self.health = SecurityOperationsHealth(self.monitor, self.outbox, self.heartbeats,
            authorize_operator=lambda value: value == "ops", ingestion_cursors=self.cursors,
            required_ingestion_sources=("authorization_audit",), clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def _complete_cursor(self):
        claim = self.cursors.claim("authorization_audit", "ingest-1")
        self.cursors.complete(claim, "ingest-1", 12)

    def test_cursor_success_is_part_of_pipeline_readiness(self):
        self._complete_cursor()
        result = self.health.snapshot("ops")
        self.assertTrue(result["checks"]["ingestion_cursors"])
        self.assertTrue(result["ingestion_cursors"]["authorization_audit"]["healthy"])

    def test_missing_cursor_fails_readiness(self):
        result = self.health.snapshot("ops")
        self.assertFalse(result["checks"]["ingestion_cursors"])
        self.assertFalse(result["ingestion_cursors"]["authorization_audit"]["initialized"])

    def test_stale_cursor_fails_readiness(self):
        self._complete_cursor()
        self.now += 301
        result = self.health.snapshot("ops")
        self.assertFalse(result["checks"]["ingestion_cursors"])
        self.assertEqual(result["ingestion_cursors"]["authorization_audit"]["age_seconds"], 301.0)

    def test_source_error_fails_readiness_without_exposing_error_text(self):
        claim = self.cursors.claim("authorization_audit", "ingest-1")
        self.cursors.fail(claim, "ingest-1", "source_unavailable")
        result = self.health.snapshot("ops")
        self.assertFalse(result["checks"]["ingestion_cursors"])
        self.assertTrue(result["ingestion_cursors"]["authorization_audit"]["error_present"])
        self.assertNotIn("source_unavailable", str(result))


if __name__ == "__main__":
    unittest.main()
