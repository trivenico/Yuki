"""Tests for fail-closed desktop session-expiry cleanup."""
import unittest

from ui.desktop.launch import (
    _close_expired_controller, _close_startup_resources,
    _shutdown_desktop_services,
)


class Controller:
    def __init__(self, error=None):
        self.error = error
        self.logout_calls = 0

    def logout(self):
        self.logout_calls += 1
        if self.error is not None:
            raise self.error


class DesktopLaunchCleanupTests(unittest.TestCase):
    def test_partial_startup_cleanup_is_reverse_order_deduplicated_and_best_effort(self):
        events = []

        class Resource:
            def __init__(self, name, fail=False):
                self.name = name
                self.fail = fail

            def close(self):
                events.append(self.name)
                if self.fail:
                    raise RuntimeError("cleanup failed")

        first = Resource("first")
        broken = Resource("broken", fail=True)
        last = Resource("last")
        _close_startup_resources((first, broken, last, first, None))
        self.assertEqual(events, ["last", "broken", "first"])

    def test_shutdown_revokes_session_before_closing_lifecycle(self):
        events = []

        class Controller:
            def logout(self):
                events.append("logout")

        class Lifecycle:
            def close(self, *, before_close):
                events.append("lifecycle-start")
                before_close()
                events.append("resources-close")

        _shutdown_desktop_services(Lifecycle(), Controller(), None)
        self.assertEqual(events, ["lifecycle-start", "logout", "resources-close"])

    def test_shutdown_uses_session_manager_when_no_controller_exists(self):
        events = []

        class Sessions:
            def logout(self):
                events.append("session-logout")

        _shutdown_desktop_services(None, None, Sessions())
        self.assertEqual(events, ["session-logout"])

    def test_shutdown_propagates_revocation_failure_to_worker_boundary(self):
        class Controller:
            def logout(self):
                raise PermissionError("revocation uncertain")

        class Lifecycle:
            def __init__(self):
                self.closed = False

            def close(self, *, before_close):
                try:
                    before_close()
                finally:
                    self.closed = True

        lifecycle = Lifecycle()
        with self.assertRaises(PermissionError):
            _shutdown_desktop_services(lifecycle, Controller(), None)
        self.assertTrue(lifecycle.closed)

    def test_no_controller_is_a_noop(self):
        self.assertIsNone(_close_expired_controller(None))

    def test_expired_controller_logout_is_attempted_once(self):
        controller = Controller()
        self.assertIsNone(_close_expired_controller(controller))
        self.assertEqual(controller.logout_calls, 1)

    def test_cleanup_error_is_returned_without_preventing_login_transition(self):
        error = OSError("local cleanup failed")
        controller = Controller(error)
        result = _close_expired_controller(controller)
        self.assertIs(result, error)
        self.assertEqual(controller.logout_calls, 1)


if __name__ == "__main__":
    unittest.main()
