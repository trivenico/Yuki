import threading
import unittest
from unittest.mock import patch

from automation.production_worker_runtime import ProductionWorkerRuntime
from automation.worker_service import load_factory, load_production_runtime, run_worker_service
from core.production_security_runtime import ProductionSecurityRuntime


class Resource:
    def __init__(self, name, events, ready=True):
        self.name, self.events, self.ready, self.closed = name, events, ready, 0

    def check_ready(self):
        return self.ready

    def close(self):
        self.closed += 1
        self.events.append("close:" + self.name)


class Security:
    def __init__(self, events):
        self.events = events
        self.audit_writer = Resource("audit", events)

    def verify_audit_integrity(self):
        self.events.append("verify:audit")
        return True

    @property
    def authorization_gateway(self):
        return type("Gateway", (), {"audit_writer": self.audit_writer})()


class WorkerServiceTests(unittest.TestCase):
    def make_runtime(self, events):
        security = ProductionSecurityRuntime(
            security=Security(events),
            workload_key_registry=Resource("registry", events),
            authorization_audit_reader=Resource("authorization_reader", events),
            workload_key_audit_reader=Resource("key_audit_reader", events),
            security_monitor=Resource("monitor", events),
            ingestion_cursors=Resource("cursors", events),
            ingestor=object(),
            alert_outbox=Resource("outbox", events),
            alert_operator_reader=Resource("alert_operator_reader", events),
            work_leases=Resource("leases", events),
            authorization_audit_checkpoint_publisher=object(),
        )
        return ProductionWorkerRuntime(
            task_runtime=object(), worker=object(), supervisor=object(),
            workflow_store=Resource("workflow", events),
            delegation_authority=Resource("delegation", events),
            security_runtime=security,
        )

    def test_factory_spec_rejects_malformed_values(self):
        for spec in ("", "module", ":factory", "module:", "module:Class.method"):
            with self.subTest(spec=spec):
                with self.assertRaises(RuntimeError):
                    load_factory(spec)

    def test_factory_loads_top_level_callable(self):
        with patch("automation.worker_service.importlib.import_module") as importer:
            importer.return_value = type("Module", (), {"build": staticmethod(lambda: "ok")})()
            self.assertEqual(load_factory("deployment.bootstrap:build")(), "ok")
            importer.assert_called_once_with("deployment.bootstrap")

    def test_loader_rejects_uncomposed_runtime(self):
        with self.assertRaisesRegex(TypeError, "bootstrap_must_return_production_worker_runtime"):
            load_production_runtime(lambda: object())

    def test_loader_closes_transferred_resources_on_readiness_failure(self):
        events = []
        runtime = self.make_runtime(events)
        runtime.workflow_store.ready = False
        with self.assertRaisesRegex(RuntimeError, "workflow_repository_not_ready"):
            load_production_runtime(lambda: runtime)
        self.assertEqual(runtime.workflow_store.closed, 1)
        self.assertEqual(runtime.delegation_authority.closed, 1)
        self.assertEqual(runtime.security_runtime.work_leases.closed, 1)
        self.assertEqual(runtime.security_runtime.security.audit_writer.closed, 1)

    def test_service_gracefully_stops_and_closes_worker_before_security(self):
        events = []
        runtime = self.make_runtime(events)
        stop = threading.Event()

        def one_cycle():
            events.append("cycle")
            stop.set()

        runtime.run_cycle = one_cycle
        self.assertEqual(run_worker_service(runtime, stop, cycle_interval_seconds=0.1), 0)
        self.assertLess(events.index("close:delegation"), events.index("close:leases"))
        self.assertLess(events.index("close:workflow"), events.index("close:audit"))
        self.assertEqual(runtime.workflow_store.closed, 1)
        self.assertEqual(runtime.security_runtime.work_leases.closed, 1)

    def test_interval_validation_happens_before_resource_ownership_transfer(self):
        events = []
        runtime = self.make_runtime(events)
        with self.assertRaisesRegex(ValueError, "worker_cycle_interval_out_of_range"):
            run_worker_service(runtime, threading.Event(), cycle_interval_seconds=0)
        self.assertFalse(any(item.startswith("close:") for item in events))


if __name__ == "__main__":
    unittest.main()
