import threading
import unittest

from defense.security_session import SecuritySessionController, SecuritySessionError


class FakeMonitor:
    def __init__(self):
        self.enabled = False
        self.disable_calls = 0
        self.snapshot_hook = None
        self.disable_hook = None

    def enable(self):
        self.enabled = True

    def disable(self):
        self.disable_calls += 1
        if self.disable_hook:
            self.disable_hook()
        self.enabled = False

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


    def test_stop_during_startup_cannot_be_undone_by_late_enable(self):
        entered = threading.Event()
        release = threading.Event()

        def delayed_enable():
            entered.set()
            release.wait(2)
            self.monitor.enabled = True

        self.monitor.enable = delayed_enable
        errors = []

        def start():
            try:
                self.controller.start(consent=True)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=start)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.controller.stop()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertFalse(self.monitor.enabled)
        self.assertFalse(self.controller.active)


    def test_snapshot_rejected_while_monitor_is_starting(self):
        entered = threading.Event()
        release = threading.Event()

        def delayed_enable():
            entered.set()
            release.wait(2)
            self.monitor.enabled = True

        self.monitor.enable = delayed_enable
        errors = []
        worker = threading.Thread(
            target=lambda: self.controller.start(consent=True)
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(SecuritySessionError):
            self.controller.snapshot()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.controller.active)

    def test_duplicate_start_during_initialization_is_rejected(self):
        entered = threading.Event()
        release = threading.Event()

        def delayed_enable():
            entered.set()
            release.wait(2)
            self.monitor.enabled = True

        self.monitor.enable = delayed_enable
        errors = []

        def start():
            try:
                self.controller.start(consent=True)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=start)
        worker.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaisesRegex(SecuritySessionError, "still starting"):
            self.controller.start(consent=True)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)


    def test_restart_waits_for_cancelled_startup_to_finish_cleanup(self):
        entered = threading.Event()
        release = threading.Event()

        def delayed_enable():
            entered.set()
            release.wait(2)
            self.monitor.enabled = True

        self.monitor.enable = delayed_enable
        errors = []

        def start():
            try:
                self.controller.start(consent=True)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=start)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.controller.stop()
        with self.assertRaisesRegex(SecuritySessionError, "starting or stopping"):
            self.controller.start(consent=True)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.monitor.enabled)
        self.assertTrue(errors)
        # A fresh start is allowed only after compensating cleanup completes.
        self.monitor.enable = lambda: setattr(self.monitor, "enabled", True)
        self.controller.start(consent=True)
        self.assertTrue(self.controller.active)
        self.assertTrue(self.monitor.enabled)

    def test_stale_snapshot_does_not_stop_new_session(self):
        entered = threading.Event()
        release = threading.Event()

        def block():
            entered.set()
            release.wait(2)

        self.controller.start(consent=True)
        self.monitor.snapshot_hook = block
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
        self.monitor.snapshot_hook = None
        self.controller.start(consent=True)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], SecuritySessionError)
        self.assertTrue(self.controller.active)
        self.assertTrue(self.monitor.enabled)


    def test_start_waits_until_stop_disable_finishes(self):
        entered = threading.Event()
        release = threading.Event()

        def delayed_disable():
            entered.set()
            release.wait(2)

        self.controller.start(consent=True)
        self.monitor.disable_hook = delayed_disable
        errors = []

        def stop():
            try:
                self.controller.stop()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=stop)
        worker.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaisesRegex(SecuritySessionError, "starting or stopping"):
            self.controller.start(consent=True)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.monitor.disable_hook = None
        self.controller.start(consent=True)
        self.assertTrue(self.controller.active)

    def test_failed_disable_requires_successful_cleanup_retry(self):
        self.controller.start(consent=True)

        def fail_disable():
            raise RuntimeError("driver busy")

        self.monitor.disable_hook = fail_disable
        with self.assertRaises(SecuritySessionError):
            self.controller.stop()
        with self.assertRaisesRegex(SecuritySessionError, "cleanup must be retried"):
            self.controller.start(consent=True)
        self.monitor.disable_hook = None
        self.controller.stop()
        self.controller.start(consent=True)
        self.assertTrue(self.controller.active)

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
