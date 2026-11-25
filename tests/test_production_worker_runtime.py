import unittest

from automation.production_worker_runtime import (
    ProductionWorkerRuntime, create_production_worker_runtime,
)
from core.production_security_runtime import ProductionSecurityRuntime


class FakeResource:
    def __init__(self, ready=True):
        self.ready = ready
        self.closed = 0

    def check_ready(self):
        return self.ready

    def close(self):
        self.closed += 1


class FakeSecurity:
    def verify_audit_integrity(self):
        return True

    def require_actor(self, actor):
        return object()

    def require_worker(self, worker):
        return object()

    @property
    def authorization_gateway(self):
        return object()


class ProductionWorkerRuntimeTests(unittest.TestCase):
    def make_security_runtime(self):
        return ProductionSecurityRuntime(
            security=FakeSecurity(), workload_key_registry=FakeResource(),
            authorization_audit_reader=FakeResource(),
            workload_key_audit_reader=FakeResource(),
            security_monitor=FakeResource(), ingestion_cursors=FakeResource(),
            ingestor=object(), alert_outbox=FakeResource(),
            work_leases=FakeResource(),
        )

    def test_factory_rejects_non_owned_security_runtime(self):
        with self.assertRaisesRegex(TypeError, "deployment_owned_production_security_runtime_required"):
            create_production_worker_runtime(
                database_url="postgresql://db",
                security_runtime=object(),
                policy_revision="revision-1",
                workload_credential_verifier=object(),
                workload_audience="yuki-worker",
                workload_credential_provider=lambda action, task, worker: "token",
                worker_id="worker-1", service_actor_id="service-1",
                handlers={"research": lambda value: value},
            )

    def test_factory_rejects_missing_worker_identity_before_opening_pools(self):
        with self.assertRaisesRegex(ValueError, "production_worker_identity_invalid"):
            create_production_worker_runtime(
                database_url="postgresql://db",
                security_runtime=self.make_security_runtime(),
                policy_revision="revision-1",
                workload_credential_verifier=object(),
                workload_audience="yuki-worker",
                workload_credential_provider=lambda action, task, worker: "token",
                worker_id=" ", service_actor_id="service-1",
                handlers={"research": lambda value: value},
            )

    def test_readiness_fails_closed_when_shared_security_runtime_is_unavailable(self):
        security = self.make_security_runtime()
        security.work_leases.ready = False
        workflow = FakeResource()
        delegation = FakeResource()
        runtime = ProductionWorkerRuntime(
            task_runtime=object(), worker=object(), supervisor=object(),
            workflow_store=workflow, delegation_authority=delegation,
            security_runtime=security,
        )
        with self.assertRaisesRegex(RuntimeError, "work_leases_not_ready"):
            runtime.verify_readiness()
        self.assertEqual(runtime._closed, False)

    def test_readiness_checks_both_separate_security_audit_readers(self):
        for attribute, error in (
            ("authorization_audit_reader", "authorization_audit_reader_not_ready"),
            ("workload_key_audit_reader", "workload_key_audit_reader_not_ready"),
        ):
            with self.subTest(reader=attribute):
                security = self.make_security_runtime()
                getattr(security, attribute).ready = False
                runtime = ProductionWorkerRuntime(
                    task_runtime=object(), worker=object(), supervisor=object(),
                    workflow_store=FakeResource(), delegation_authority=FakeResource(),
                    security_runtime=security,
                )
                with self.assertRaisesRegex(RuntimeError, error):
                    runtime.verify_readiness()

    def test_close_is_idempotent_and_closes_only_worker_owned_resources(self):
        security = self.make_security_runtime()
        workflow = FakeResource()
        delegation = FakeResource()
        runtime = ProductionWorkerRuntime(
            task_runtime=object(), worker=object(), supervisor=object(),
            workflow_store=workflow, delegation_authority=delegation,
            security_runtime=security,
        )
        runtime.close()
        runtime.close()
        self.assertEqual(workflow.closed, 1)
        self.assertEqual(delegation.closed, 1)
        self.assertEqual(security.work_leases.closed, 0)


if __name__ == "__main__":
    unittest.main()
