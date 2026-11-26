import unittest
from unittest.mock import patch

from core.authorization import AuthorizationDecision
from core.authorization_audit import AuthorizationAuditCheckpoint
from core.production_security_runtime import create_production_security_runtime
from core.authorization_decision_receipts import VerifiedAuthorizationDecisionEvidenceRecorder


class Policy:
    def authorize(self, request):
        return AuthorizationDecision(True, "decision-1", "revision-1", "allowed")


class FakeDecisionEvidenceRecorder:
    def __init__(self):
        self.calls = []

    def record_decision(self, request, decision, *, decision_hash):
        self.calls.append((request, decision))
        return "receipt-1"


class FakeReceiptSource:
    def fetch_receipts(self, decision_hashes):
        return ()


class FakeIssuerKeyResolver:
    def public_key_bytes(self, issuer_key_id):
        return bytes(32)


class FakeEvidenceVerifier:
    def __init__(self):
        self.calls = []

    def verify_batch(self, previous, current, events):
        self.calls.append((previous, current, events))
        return True


class Anchor:
    def latest(self):
        return AuthorizationAuditCheckpoint(0, "0" * 64, "2026-11-24T00:00:00+00:00")

    def publish(self, checkpoint):
        raise AssertionError("bootstrap must not publish an anchor")


class FakeAudit:
    def __init__(self, closed):
        self.closed = closed

    def __call__(self, request, decision):
        return 1

    def verify_chain(self):
        return True

    def head_hash(self):
        return "0" * 64

    def checkpoint(self):
        return AuthorizationAuditCheckpoint(0, "0" * 64, "2026-11-24T00:00:00+00:00")

    def verify_anchor(self, anchor, **kwargs):
        checkpoint = anchor.latest()
        return (
            isinstance(checkpoint, AuthorizationAuditCheckpoint)
            and checkpoint.event_id == 0
            and checkpoint.event_hash == "0" * 64
        )

    def publish_checkpoint(self, anchor):
        raise AssertionError("bootstrap must not publish an anchor")

    def event_page(self, **kwargs):
        return ()

    def evidence_page(self, **kwargs):
        return ()

    def check_ready(self):
        return True

    def close(self):
        self.closed.append("audit")


class FakeResource:
    def __init__(self, name, closed, ready=True):
        self.name = name
        self.closed = closed
        self.ready = ready
        self.health_calls = []

    def check_ready(self):
        return self.ready

    def claim(self, source, worker_id):
        return {"source": source, "event_id": 0, "lease_token": "test-lease"}

    def complete(self, claim, worker_id, event_id):
        return event_id

    def fail(self, claim, worker_id, error_code="source_ingestion_failed"):
        return None

    def audit_events(self, administrator, *, after_event_id=0, limit=500):
        return ()

    def ingest_authorization_audit(self, audit_log, *, after_event_id=0, limit=500):
        return after_event_id

    def ingest_key_lifecycle(self, key_registry, *, after_event_id=0, limit=500, **kwargs):
        return after_event_id

    def snapshot(self):
        return {"authorization_audit": {"event_id": 17, "lease_active": False}}

    def health_snapshot(self, operator):
        self.health_calls.append(operator)
        if operator != "security-admin":
            raise PermissionError("alert_operator_forbidden")
        return {
            "pending": 2, "inflight": 1, "delivered": 8,
            "dead": 0, "expired_leases": 0,
            "oldest_pending_age_seconds": 12.5,
        }

    def close(self):
        self.closed.append(self.name)


class ProductionSecurityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.closed = []
        self.audit = FakeAudit(self.closed)
        self.registry = FakeResource("registry", self.closed)
        self.authorization_reader = FakeResource("authorization_reader", self.closed)
        self.key_audit_reader = FakeResource("key_audit_reader", self.closed)
        self.monitor = FakeResource("monitor", self.closed)
        self.cursors = FakeResource("cursors", self.closed)
        self.outbox = FakeResource("outbox", self.closed)
        self.alert_operator_reader = FakeResource("alert_operator_reader", self.closed)
        self.leases = FakeResource("leases", self.closed)
        self.patches = [
            patch("core.production_security_runtime.create_authorization_audit_repository", return_value=self.audit),
            patch("core.production_security_runtime.create_workload_key_registry", return_value=self.registry),
            patch("core.production_security_runtime.create_security_event_monitor", return_value=self.monitor),
            patch("core.postgres_security_audit_readers.PostgresAuthorizationAuditReader", return_value=self.authorization_reader),
            patch("core.postgres_security_audit_readers.PostgresWorkloadKeyAuditReader", return_value=self.key_audit_reader),
            patch("core.postgres_security_event_ingestion.PostgresSecurityEventIngestionCursors", return_value=self.cursors),
            patch("core.postgres_security_alert_outbox.PostgresSecurityAlertOutbox", return_value=self.outbox),
            patch("core.postgres_security_alert_operator_reader.PostgresSecurityAlertOperatorReader", return_value=self.alert_operator_reader),
            patch("core.postgres_security_lease_store.PostgresSecurityLeaseStore", return_value=self.leases),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for item in reversed(self.patches):
            item.stop()

    def create(self, **overrides):
        config = {
            "storage_backend": "postgres",
            "database_urls": {
                "authorization_audit": "postgresql://audit/db",
                "authorization_audit_reader": "postgresql://audit-reader/db",
                "workload_key_registry": "postgresql://keys/db",
                "workload_key_audit_reader": "postgresql://key-reader/db",
                "security_monitor": "postgresql://monitor/db",
                "ingestion_cursors": "postgresql://ingestion/db",
                "alert_outbox": "postgresql://outbox/db",
                "alert_operator_reader": "postgresql://alert-operator-reader/db",
                "work_leases": "postgresql://leases/db",
            },
        }
        config.update(overrides.pop("config", {}))
        args = {
            "policy": Policy(),
            "audit_anchor": Anchor(),
            "audit_evidence_verifier": FakeEvidenceVerifier(),
            "decision_evidence_recorder": VerifiedAuthorizationDecisionEvidenceRecorder(
                FakeDecisionEvidenceRecorder(), FakeReceiptSource(), FakeIssuerKeyResolver(),
            ),
            "principal_for_actor": lambda actor: __import__("core.authorization", fromlist=["Principal"]).Principal(actor, "human"),
            "principal_for_worker": lambda worker: __import__("core.authorization", fromlist=["Principal"]).Principal(worker, "workload", "tenant-1", "agent-1"),
            "policy_revision": "revision-1",
            "authorize_admin": lambda _operator: True,
            "authorize_operator": lambda _operator: True,
        }
        args.update(overrides)
        return create_production_security_runtime(config, **args)

    def test_requires_postgres_for_shared_production_runtime(self):
        with self.assertRaisesRegex(ValueError, "requires_postgres"):
            self.create(config={"storage_backend": "sqlite"})

    def test_requires_all_nine_distinct_database_identities(self):
        with self.assertRaisesRegex(ValueError, "database_urls_required"):
            self.create(config={"storage_backend": "postgres"})
        incomplete = {
            "storage_backend": "postgres",
            "database_urls": {"authorization_audit": "postgresql://audit/db"},
        }
        with self.assertRaisesRegex(ValueError, "database_identity_required"):
            self.create(config=incomplete)
        duplicate = {
            "storage_backend": "postgres",
            "database_urls": {
                name: "postgresql://shared/db"
                for name in (
                    "authorization_audit", "authorization_audit_reader",
                    "workload_key_registry", "workload_key_audit_reader",
                    "security_monitor", "ingestion_cursors", "alert_outbox", "alert_operator_reader", "work_leases",
                )
            },
        }
        with self.assertRaisesRegex(ValueError, "identities_must_be_distinct"):
            self.create(config=duplicate)

    def test_routes_each_repository_to_its_own_database_identity(self):
        runtime = self.create()
        audit_factory = self.mocks[0]
        registry_factory = self.mocks[1]
        monitor_factory = self.mocks[2]
        self.assertEqual(audit_factory.call_args.args[0]["database_url"], "postgresql://audit/db")
        self.assertEqual(registry_factory.call_args.args[0]["database_url"], "postgresql://keys/db")
        self.assertEqual(monitor_factory.call_args.args[0]["database_url"], "postgresql://monitor/db")
        for factory in (audit_factory, registry_factory, monitor_factory):
            self.assertNotIn("database_urls", factory.call_args.args[0])
        reader_type = self.mocks[3]
        key_reader_type = self.mocks[4]
        self.assertEqual(self.mocks[5].call_args.args[0], "postgresql://ingestion/db")
        self.assertEqual(self.mocks[6].call_args.args[0], "postgresql://outbox/db")
        self.assertEqual(self.mocks[7].call_args.args[0], "postgresql://alert-operator-reader/db")
        self.assertEqual(self.mocks[8].call_args.args[0], "postgresql://leases/db")
        self.assertEqual(reader_type.call_args.args[0], "postgresql://audit-reader/db")
        self.assertEqual(key_reader_type.call_args.args[0], "postgresql://key-reader/db")
        self.assertNotIn("database_urls", runtime.authorization_audit_reader.__dict__)
        self.assertIs(runtime.ingestor.key_registry, runtime.workload_key_audit_reader)
        self.assertIs(runtime.security.authorization_gateway.audit_writer, self.audit)
        self.assertIsNotNone(runtime.security.authorization_gateway._evidence_recorder)
        runtime.close()

    def test_checkpoint_publisher_is_wired_and_closed_runtime_rejects_publication(self):
        runtime = self.create()
        self.assertIsNotNone(runtime.authorization_audit_checkpoint_publisher)
        runtime.close()
        with self.assertRaisesRegex(RuntimeError, "runtime_closed"):
            runtime.publish_authorization_audit_checkpoint()

    def test_runtime_requires_independent_decision_evidence_recorder(self):
        with self.assertRaisesRegex(TypeError, "verified_independent_authorization_decision_evidence_recorder_required"):
            self.create(decision_evidence_recorder=FakeDecisionEvidenceRecorder())

    def test_runtime_requires_independent_evidence_verifier(self):
        with self.assertRaisesRegex(TypeError, "independent_authorization_audit_evidence_verifier_required"):
            self.create(audit_evidence_verifier=object())

    def test_composes_all_repositories_and_closes_in_reverse_order_once(self):
        runtime = self.create()
        runtime.verify_readiness()
        runtime.close()
        runtime.close()
        self.assertEqual(
            self.closed,
            ["leases", "alert_operator_reader", "outbox", "cursors", "monitor", "key_audit_reader", "authorization_reader", "registry", "audit"],
        )

    def test_startup_readiness_failure_closes_every_created_resource(self):
        self.leases.ready = False
        with self.assertRaisesRegex(RuntimeError, "work_leases_not_ready"):
            self.create()
        self.assertEqual(
            self.closed,
            ["leases", "alert_operator_reader", "outbox", "cursors", "monitor", "key_audit_reader", "authorization_reader", "registry", "audit"],
        )

    def test_readiness_rejects_runtime_after_shutdown(self):
        runtime = self.create()
        runtime.close()
        closed_resources = list(self.closed)
        with self.assertRaisesRegex(RuntimeError, "production_security_runtime_closed"):
            runtime.verify_readiness()
        self.assertEqual(self.closed, closed_resources)

    def test_operator_snapshot_exposes_recovery_state_without_alert_payloads(self):
        runtime = self.create()
        snapshot = runtime.operational_snapshot("security-admin")
        self.assertEqual(snapshot["ingestion"]["authorization_audit"]["event_id"], 17)
        self.assertEqual(snapshot["alerts"]["pending"], 2)
        self.assertEqual(self.alert_operator_reader.health_calls, ["security-admin"])
        self.assertEqual(self.outbox.health_calls, [])
        self.assertEqual(snapshot["alerts"]["oldest_pending_age_seconds"], 12.5)
        self.assertNotIn("payload", snapshot["alerts"])
        with self.assertRaises(PermissionError):
            runtime.operational_snapshot("intruder")
        runtime.close()
        with self.assertRaisesRegex(RuntimeError, "runtime_closed"):
            runtime.operational_snapshot("security-admin")

    def test_anchor_failure_closes_audit_before_any_other_resource_exists(self):
        class InvalidAnchor:
            def latest(self):
                return None
            def publish(self, checkpoint):
                pass
        with self.assertRaisesRegex(RuntimeError, "audit integrity verification failed"):
            self.create(audit_anchor=InvalidAnchor())
        self.assertEqual(self.closed, ["audit"])


    def test_startup_database_outage_propagates_and_closes_all_created_resources(self):
        def unavailable():
            raise ConnectionError("database_unavailable")
        self.leases.check_ready = unavailable
        with self.assertRaisesRegex(ConnectionError, "database_unavailable"):
            self.create()
        self.assertEqual(
            self.closed,
            ["leases", "alert_operator_reader", "outbox", "cursors", "monitor", "key_audit_reader", "authorization_reader", "registry", "audit"],
        )

    def test_readiness_fails_closed_when_durable_repository_becomes_unavailable(self):
        runtime = self.create()
        def unavailable():
            raise ConnectionError("database_unavailable")
        self.monitor.check_ready = unavailable
        with self.assertRaisesRegex(ConnectionError, "database_unavailable"):
            runtime.verify_readiness()
        runtime.close()
        self.assertEqual(
            self.closed,
            ["leases", "alert_operator_reader", "outbox", "cursors", "monitor", "key_audit_reader", "authorization_reader", "registry", "audit"],
        )


if __name__ == "__main__":
    unittest.main()
