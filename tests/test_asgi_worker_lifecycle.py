import asyncio
import json
import unittest

from api.runtime import ConversationRuntime


class FakeApplication:
    def __init__(self, order):
        self.order = order
        self.store = object()

    def close(self):
        self.order.append("application")


class FakeSecurity:
    def verify_audit_integrity(self):
        return True


class FakeSecurityRuntime:
    def __init__(self, security, order, ready=True):
        self.security = security
        self.order = order
        self.ready = ready
        self.readiness_calls = 0

    def verify_readiness(self):
        self.readiness_calls += 1
        if not self.ready:
            raise ConnectionError("security_dependency_unavailable")

    def close(self):
        self.order.append("security")


class FakeWorkerRuntime:
    def __init__(self, order, security_runtime, ready=True):
        self.order = order
        self.security_runtime = security_runtime
        self.ready = ready
        self.readiness_calls = 0

    def verify_readiness(self):
        self.readiness_calls += 1
        self.security_runtime.verify_readiness()
        if not self.ready:
            raise ConnectionError("worker_dependency_unavailable")

    def close(self):
        self.order.append("worker")


class ASGIWorkerLifecycleTests(unittest.TestCase):
    def make_runtime(self, *, worker_ready=True, security_ready=True):
        order = []
        security = FakeSecurity()
        security_runtime = FakeSecurityRuntime(security, order, security_ready)
        worker_runtime = FakeWorkerRuntime(order, security_runtime, worker_ready)
        runtime = ConversationRuntime(
            object(), FakeApplication(order),
            production_security=security,
            production_security_runtime=security_runtime,
            production_worker_runtime=worker_runtime,
        )
        return runtime, order, security_runtime, worker_runtime

    def test_worker_runtime_requires_owned_security_runtime(self):
        with self.assertRaisesRegex(ValueError, "requires_owned_security_runtime"):
            ConversationRuntime(
                object(), FakeApplication([]),
                production_security=FakeSecurity(),
                production_worker_runtime=FakeWorkerRuntime([], None),
            )

    def test_startup_checks_both_runtimes_and_shutdown_closes_in_reverse_order(self):
        runtime, order, security, worker = self.make_runtime()
        sent = []
        messages = iter([
            {"type": "lifespan.startup"},
            {"type": "lifespan.shutdown"},
        ])

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        asyncio.run(runtime({"type": "lifespan"}, receive, send))
        self.assertEqual(
            [item["type"] for item in sent],
            ["lifespan.startup.complete", "lifespan.shutdown.complete"],
        )
        self.assertEqual(security.readiness_calls, 1)
        self.assertEqual(worker.readiness_calls, 1)
        self.assertEqual(order, ["application", "worker", "security"])

    def test_worker_readiness_failure_fails_startup_and_closes_everything(self):
        runtime, order, security, worker = self.make_runtime(worker_ready=False)
        sent = []
        messages = iter([{"type": "lifespan.startup"}])

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        asyncio.run(runtime({"type": "lifespan"}, receive, send))
        self.assertEqual(sent[0]["type"], "lifespan.startup.failed")
        self.assertEqual(order, ["application", "worker", "security"])

    def test_ready_endpoint_reports_worker_dependency_failure(self):
        runtime, _order, _security, worker = self.make_runtime(worker_ready=False)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(runtime(
            {"type": "http", "method": "GET", "path": "/health/ready"},
            receive, send,
        ))
        self.assertEqual(sent[0]["status"], 503)
        payload = json.loads(sent[1]["body"])
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(worker.readiness_calls, 1)


if __name__ == "__main__":
    unittest.main()
