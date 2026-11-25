from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import run_production_web


class FakeSecurityRuntime:
    def __init__(self, ready=True):
        self.ready = ready
        self.closed = False

    def verify_readiness(self):
        if not self.ready:
            raise RuntimeError("not_ready")

    def close(self):
        self.closed = True


class FakeASGIRuntime:
    def __init__(self, security):
        self.production_security_runtime = security
        self.closed = False

    async def __call__(self, scope, receive, send):
        return None

    def close(self):
        self.closed = True


class ProductionWebRunnerTests(unittest.TestCase):
    def test_load_requires_owned_production_security_runtime(self):
        with patch.object(run_production_web, "ProductionSecurityRuntime", FakeSecurityRuntime):
            runtime = FakeASGIRuntime(FakeSecurityRuntime())
            self.assertIs(run_production_web.load_production_runtime(lambda: runtime), runtime)

    def test_load_closes_runtime_when_readiness_fails(self):
        with patch.object(run_production_web, "ProductionSecurityRuntime", FakeSecurityRuntime):
            runtime = FakeASGIRuntime(FakeSecurityRuntime(ready=False))
            with self.assertRaisesRegex(RuntimeError, "not_ready"):
                run_production_web.load_production_runtime(lambda: runtime)
            self.assertTrue(runtime.closed)

    def test_load_rejects_runtime_without_security_and_closes_it(self):
        class InvalidRuntime:
            def __init__(self):
                self.closed = False

            def __call__(self, scope, receive, send):
                return None

            def close(self):
                self.closed = True

        runtime = InvalidRuntime()
        with patch.object(run_production_web, "ProductionSecurityRuntime", FakeSecurityRuntime):
            with self.assertRaisesRegex(TypeError, "owned_production_security_runtime"):
                run_production_web.load_production_runtime(lambda: runtime)
        self.assertTrue(runtime.closed)

    def test_server_is_single_worker_loopback_and_closes_runtime(self):
        runtime = FakeASGIRuntime(FakeSecurityRuntime())
        calls = []

        def server_runner(app, **kwargs):
            calls.append((app, kwargs))

        result = run_production_web.run_web_service(
            runtime, server_runner=server_runner,
        )
        self.assertEqual(result, 0)
        self.assertEqual(calls[0][0], runtime)
        self.assertEqual(calls[0][1]["host"], "127.0.0.1")
        self.assertEqual(calls[0][1]["workers"], 1)
        self.assertEqual(calls[0][1]["lifespan"], "on")
        self.assertFalse(calls[0][1]["proxy_headers"])
        self.assertTrue(runtime.closed)

    def test_server_rejects_non_loopback_listener(self):
        runtime = FakeASGIRuntime(FakeSecurityRuntime())
        with self.assertRaisesRegex(ValueError, "bind_loopback_only"):
            run_production_web.run_web_service(runtime, host="0.0.0.0")
        self.assertFalse(runtime.closed)

    def test_server_closes_runtime_when_server_runner_raises(self):
        runtime = FakeASGIRuntime(FakeSecurityRuntime())

        def failing_runner(*args, **kwargs):
            raise RuntimeError("server_failed")

        with self.assertRaisesRegex(RuntimeError, "server_failed"):
            run_production_web.run_web_service(runtime, server_runner=failing_runner)
        self.assertTrue(runtime.closed)


if __name__ == "__main__":
    unittest.main()
