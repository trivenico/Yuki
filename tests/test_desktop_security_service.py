import threading
import unittest

from ui.desktop.security_service import (
    DesktopSecurityService,
    DesktopSecurityServiceError,
    SecuritySessionError,
)


class FakeController:
    def __init__(self, monitor, *, authorize):
        self.monitor = monitor
        self.authorize = authorize
        self.active = False
        self.closed = False

    def start(self, *, consent):
        if consent is not True or self.authorize() is not True:
            raise RuntimeError("denied")
        self.active = True

    def snapshot(self):
        if not self.active or self.authorize() is not True:
            raise SecuritySessionError("inactive")
        return {
            "enabled": True,
            "read_only": True,
            "persisted": False,
            "automated_response": False,
            "processes": [],
            "alerts": [],
        }

    def stop(self):
        self.active = False

    def close(self):
        self.active = False
        self.closed = True


class DesktopSecurityServiceTests(unittest.TestCase):
    def setUp(self):
        self.authorized = True
        self.factory_calls = 0
        self.controller = None

        def monitor_factory():
            self.factory_calls += 1
            return object()

        def controller_factory(monitor, *, authorize):
            self.controller = FakeController(monitor, authorize=authorize)
            return self.controller

        self.service = DesktopSecurityService(
            authorize=lambda: self.authorized,
            monitor_factory=monitor_factory,
            controller_factory=controller_factory,
        )

    def test_construction_and_status_do_not_construct_monitor(self):
        self.assertFalse(self.service.active)
        self.assertFalse(self.service.configured)
        self.assertEqual(self.factory_calls, 0)

    def test_consent_and_authorization_gate_factory(self):
        with self.assertRaises(SecuritySessionError):
            self.service.start(consent=False)
        self.authorized = False
        with self.assertRaises(SecuritySessionError):
            self.service.start(consent=True)
        self.assertEqual(self.factory_calls, 0)

    def test_explicit_start_snapshot_stop(self):
        self.service.start(consent=True)
        self.assertTrue(self.service.active)
        self.assertEqual(self.factory_calls, 1)
        result = self.service.snapshot()
        self.assertTrue(result["read_only"])
        self.assertFalse(result["persisted"])
        self.service.stop()
        self.assertFalse(self.service.active)
        with self.assertRaises(SecuritySessionError):
            self.service.snapshot()

    def test_start_failure_keeps_controller_for_cleanup(self):
        self.authorized = False
        with self.assertRaises(SecuritySessionError):
            self.service.start(consent=True)
        self.assertEqual(self.factory_calls, 0)

    def test_close_is_idempotent_and_prevents_restart(self):
        self.service.start(consent=True)
        controller = self.controller
        self.service.close()
        self.assertTrue(controller.closed)
        self.assertFalse(self.service.active)
        self.service.close()
        with self.assertRaises(SecuritySessionError):
            self.service.start(consent=True)
        self.assertEqual(self.factory_calls, 1)

    def test_close_before_start_never_constructs_monitor(self):
        self.service.close()
        self.assertEqual(self.factory_calls, 0)
        with self.assertRaises(SecuritySessionError):
            self.service.start(consent=True)


if __name__ == "__main__":
    unittest.main()
