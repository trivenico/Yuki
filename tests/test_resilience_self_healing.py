import unittest

from core.resilience import ResilienceMonitor
from core.self_healing import SelfHealingEngine


class ResilienceMonitorTests(unittest.TestCase):
    def test_healthy_check(self):
        report = ResilienceMonitor([("ready", lambda: True)]).run()
        self.assertEqual(report.status, "healthy")
        self.assertEqual(report.checks[0].status, "healthy")

    def test_false_check_is_unhealthy(self):
        report = ResilienceMonitor([("ready", lambda: False)]).run()
        self.assertEqual(report.status, "unhealthy")

    def test_exception_is_sanitized(self):
        def broken():
            raise RuntimeError("secret-token")
        report = ResilienceMonitor([("broken", broken)]).run()
        self.assertEqual(report.status, "error")
        self.assertNotIn("secret-token", report.checks[0].detail)

    def test_duplicate_names_rejected(self):
        monitor = ResilienceMonitor([("ready", lambda: True)])
        with self.assertRaises(ValueError):
            monitor.register("ready", lambda: True)


class SelfHealingTests(unittest.TestCase):
    def test_missing_approval_provider_fails_closed(self):
        calls = []
        engine = SelfHealingEngine()
        engine.register("restart_demo", lambda: calls.append("ran") or True)
        result = engine.execute_approved(engine.propose("restart_demo", "test"))
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(calls, [])

    def test_denial_does_not_execute(self):
        calls = []
        engine = SelfHealingEngine(lambda proposal: False)
        engine.register("restart_demo", lambda: calls.append("ran") or True)
        result = engine.execute_approved(engine.propose("restart_demo", "test"))
        self.assertEqual(result.status, "denied")
        self.assertEqual(calls, [])

    def test_approval_executes_registered_action(self):
        calls = []
        engine = SelfHealingEngine(lambda proposal: True)
        engine.register("recover", lambda: calls.append("ran") or True, risk="low")
        result = engine.execute_approved(engine.propose("recover", "approved test"))
        self.assertTrue(result.ok)
        self.assertEqual(calls, ["ran"])

    def test_unknown_action_rejected(self):
        engine = SelfHealingEngine(lambda proposal: True)
        with self.assertRaises(KeyError):
            engine.propose("unknown", "test")


if __name__ == "__main__":
    unittest.main()
