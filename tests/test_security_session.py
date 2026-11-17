import threading
import unittest

from defense.security_session import SecuritySessionController, SecuritySessionError


class FakeMonitor:
    def __init__(self):
        self.enabled = False
        self.disable_calls = 0
        self.snapshot_hook = None

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False
        self.disable_calls += 1

    def snapshot(self):
        if self.snapshot_hook:
            self.snapshot_hook()
        return {
            "enabled": True, "read_only": True, "persisted": False,
            "automated_response": False, "processes": [], "alerts": [],
        }


class SecuritySessionControllerTests(unittest.TestCase):
    def setUp(self):
        self.authorized = True
        self.monitor = FakeMonitor()
        self.controller = SecuritySessionController(
            self.monitor, authorize=lambda: self.authorized,
        )

    def test_requires_explicit_consent_and_authenticated_session(self):
        with self.assertRaises(SecuritySessionError):
            self.controller.start(consent=False)
        self.authorized = False
        with self.assertRaises(SecuritySessionError):
            self.controller.start(consent=True)
        self.assertFalse(self.monitor.enabled)

    def test_start_snapshot_stop_lifecycle(self):
        self.controller.start(consent=True)
        self.assertTrue(self.controller.active)
        self.assertTrue(self.monitor.enabled)
        self.assertTrue(self.controller.snapshot()["read_only"])
        self.controller.stop()
        self.assertFalse(self.controller.active)
        self.assertFalse(self.monitor.enabled)
        with self.assertRaises(SecuritySessionError):
            self.controller.snapshot()

    def test_expired_auth_revokes_monitoring(self):
        self.controller.start(consent=True)
        self.authorized = False
        with self.assertRaises(SecuritySessionError):
            self.controller.snapshot()
        self.assertFalse(self.monitor.enabled)
        self.assertFalse(self.controller.active)

    def test_stop_invalidates_inflight_snapshot(self):
        entered = threading.Event()
        release = threading.Event()

        def block():
            entered.set()
            release.wait(2)

        self.monitor.snapshot_hook = block
        self.controller.start(consent=True)
        errors = []

        def collect():
            try:
                self.controller.snapshot()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=collect)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.controller.stop()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], SecuritySessionError)

    def test_close_is_terminal(self):
        self.controller.start(consent=True)
        self.controller.close()
        self.assertFalse(self.monitor.enabled)
        with self.assertRaises(SecuritySessionError):
            self.controller.start(consent=True)

    def test_rejects_monitor_snapshot_that_claims_automation(self):
        self.controller.start(consent=True)
        self.monitor.snapshot = lambda: {
            "enabled": True, "read_only": True, "persisted": False,
            "automated_response": True,
        }
        with self.assertRaises(SecuritySessionError):
            self.controller.snapshot()


if __name__ == "__main__":
    unittest.main()
