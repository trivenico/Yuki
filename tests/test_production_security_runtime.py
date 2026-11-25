import unittest
from unittest.mock import patch

from core.authorization import AuthorizationDecision
from core.authorization_audit import AuthorizationAuditCheckpoint
from core.production_security_runtime import create_production_security_runtime


class Policy:
    def authorize(self, request):
        return AuthorizationDecision(True, "decision-1", "revision-1", "allowed")


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

    def check_ready(self):
        return True

    def close(self):
        self.closed.append("audit")


class FakeResource:
    def __init__(self, name, closed, ready=True):
        self.name = name
        self.closed = closed
        self.ready = ready

    def check_ready(self):
        return self.ready

    def audit_events(self, administrator, *, after_event_id=0, limit=500):
        return ()

    def snapshot(self):
        return {"authorization_audit": {"event_id": 17, "lease_active": False}}

    def health_snapshot(self, operator):
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
        self.monitor = FakeResource("monitor", self.closed)
        self.cursors = FakeResource("cursors", self.closed)
        self.outbox = FakeResource("outbox", self.closed)
        self.leases = FakeResource("leases", self.closed)
        self.patches = [
            patch("core.production_security_runtime.create_authorization_audit_repository", return_value=self.audit),
            patch("core.production_security_runtime.create_workload_key_registry", return_value=self.registry),
            patch("core.production_security_runtime.create_security_event_monitor", return_value=self.monitor),
            patch("core.postgres_security_event_ingestion.PostgresSecurityEventIngestionCursors", return_value=self.cursors),
            patch("core.postgres_security_alert_outbox.PostgresSecurityAlertOutbox", return_value=self.outbox),
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
                "workload_key_registry": "postgresql://keys/db",
                "security_monitor": "postgresql://monitor/db",
                "ingestion_cursors": "postgresql://ingestion/db",
                "alert_outbox": "postgresql://outbox/db",
                "work_leases": "postgresql://leases/db",
            },
        }
        config.update(overrides.pop("config", {}))
        args = {
            "policy": Policy(),
            "audit_anchor": Anchor(),
            "principal_for_actor": lambda actor: __import__("core.authorization", fromlist=["Principal"]).Principal(actor, "human"),
            "principal_for_worker": lambda worker: __import__("core.authorization", fromlist=["Principal"]).Principal(worker, "workload", "tenant-1", "agent-1"),
            "policy_revision": "revision-1",
            "authorize_admin": lambda _operator: True,
            "authorize_operator": lambda _operator: True,
            "key_audit_operator": "security-admin",
        }
        args.update(overrides)
        return create_production_security_runtime(config, **args)

    def test_requires_postgres_for_shared_production_runtime(self):
        with self.assertRaisesRegex(ValueError, "requires_postgres"):
            self.create(config={"storage_backend": "sqlite"})

    def test_requires_all_six_distinct_database_identities(self):
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
                    "authorization_audit", "workload_key_registry", "security_monitor",
                    "ingestion_cursors", "alert_outbox", "work_leases",
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
        self.assertEqual(self.mocks[3].call_args.args[0], "postgresql://ingestion/db")
        self.assertEqual(self.mocks[4].call_args.args[0], "postgresql://outbox/db")
        self.assertEqual(self.mocks[5].call_args.args[0], "postgresql://leases/db")
        runtime.close()

    def test_composes_all_repositories_and_closes_in_reverse_order_once(self):
        runtime = self.create()
        runtime.verify_readiness()
        runtime.close()
        runtime.close()
        self.assertEqual(
            self.closed,
            ["leases", "outbox", "cursors", "monitor", "registry", "audit"],
        )

    def test_startup_readiness_failure_closes_every_created_resource(self):
        self.leases.ready = False
        with self.assertRaisesRegex(RuntimeError, "work_leases_not_ready"):
            self.create()
        self.assertEqual(
            self.closed,
            ["leases", "outbox", "cursors", "monitor", "registry", "audit"],
        )

    def test_operator_snapshot_exposes_recovery_state_without_alert_payloads(self):
        runtime = self.create()
        snapshot = runtime.operational_snapshot("security-admin")
        self.assertEqual(snapshot["ingestion"]["authorization_audit"]["event_id"], 17)
        self.assertEqual(snapshot["alerts"]["pending"], 2)
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
            ["leases", "outbox", "cursors", "monitor", "registry", "audit"],
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
            ["leases", "outbox", "cursors", "monitor", "registry", "audit"],
        )


if __name__ == "__main__":
    unittest.main()
