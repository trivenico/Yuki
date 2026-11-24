import tempfile
import unittest
from pathlib import Path

from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
from core.authorization_audit import AuthorizationAuditCheckpoint, SQLiteAuthorizationAuditLog
from core.authorization_audit_maintenance import AuthorizationAuditMaintenance


class MemoryAnchor:
    def __init__(self):
        self.value = None
        self.fail_publish = False
        self.fail_latest = False

    def publish(self, checkpoint):
        if self.fail_publish:
            raise OSError("anchor unavailable")
        if self.value is not None and checkpoint.event_id < self.value.event_id:
            raise ValueError("checkpoint rollback")
        self.value = checkpoint

    def latest(self):
        if self.fail_latest:
            raise OSError("anchor unavailable")
        return self.value


class AuthorizationAuditMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.audit = SQLiteAuthorizationAuditLog(
            str(Path(self.directory.name) / "authorization-audit.sqlite3")
        )
        self.anchor = MemoryAnchor()
        self.maintenance = AuthorizationAuditMaintenance(self.audit, self.anchor)

    def tearDown(self):
        self.directory.cleanup()

    def append_event(self, suffix):
        self.audit(
            AuthorizationRequest(
                Principal("worker", "workload", tenant_id="tenant-1", agent_id="agent-1"),
                "task:read", "task:" + suffix,
            ),
            AuthorizationDecision(True, "decision-" + suffix, "revision-1", "allowed"),
        )

    def test_requires_explicit_initial_anchor_provisioning(self):
        with self.assertRaisesRegex(RuntimeError, "anchor_not_provisioned"):
            self.maintenance.run_once()

    def test_publishes_new_head_and_verifies_it(self):
        self.anchor.publish(self.audit.checkpoint())
        self.append_event("one")
        report = self.maintenance.run_once()
        self.assertTrue(report.published)
        self.assertTrue(report.verified)
        self.assertEqual(report.checkpoint.event_id, 1)
        self.assertTrue(self.audit.verify_anchor(self.anchor))

    def test_skips_publication_when_checkpoint_is_current(self):
        self.anchor.publish(self.audit.checkpoint())
        report = self.maintenance.run_once()
        self.assertFalse(report.published)
        self.assertTrue(report.verified)

    def test_refuses_local_chain_corruption(self):
        self.anchor.publish(self.audit.checkpoint())
        self.append_event("one")
        with self.audit._connect() as db:
            db.execute("UPDATE authorization_audit_events SET reason_code='tampered'")
        with self.assertRaisesRegex(RuntimeError, "local_chain_invalid"):
            self.maintenance.run_once()

    def test_anchor_outage_fails_closed(self):
        self.anchor.publish(self.audit.checkpoint())
        self.anchor.fail_latest = True
        with self.assertRaisesRegex(RuntimeError, "anchor_unavailable"):
            self.maintenance.run_once()

    def test_publish_failure_fails_closed(self):
        self.anchor.publish(self.audit.checkpoint())
        self.append_event("one")
        self.anchor.fail_publish = True
        with self.assertRaisesRegex(RuntimeError, "checkpoint_publish_failed"):
            self.maintenance.run_once()

    def test_rejects_checkpoint_rollback_or_mismatch(self):
        self.anchor.publish(AuthorizationAuditCheckpoint(5, "f" * 64))
        with self.assertRaisesRegex(RuntimeError, "anchor_mismatch"):
            self.maintenance.run_once()


if __name__ == "__main__":
    unittest.main()
