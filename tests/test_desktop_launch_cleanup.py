"""Tests for fail-closed desktop session-expiry cleanup."""
import unittest

from ui.desktop.launch import (
    _close_expired_controller, _close_startup_resources,
    _shutdown_desktop_services, _run_cleanup_steps,
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
    def test_cleanup_steps_attempt_all_operations_and_return_first_error(self):
        events = []
        first_error = RuntimeError("security cleanup failed")

        def fail_security_cleanup():
            events.append("security-close")
            raise first_error

        def revoke_desktop_session():
            events.append("desktop-logout")

        result = _run_cleanup_steps(fail_security_cleanup, revoke_desktop_session)
        self.assertIs(result, first_error)
        self.assertEqual(events, ["security-close", "desktop-logout"])

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
        self.assertEqual(events, ["first", "last", "broken"])

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

    def test_failed_logout_locks_every_session_action_except_retry(self):
        from ui.desktop.launch import _restrict_session_to_logout_retry

        class Tabs:
            def __init__(self):
                self.enabled = [True, True, True]
            def count(self):
                return len(self.enabled)
            def setTabEnabled(self, index, enabled):
                self.enabled[index] = enabled

        class Control:
            def __init__(self):
                self.enabled = True
                self.text = ""
            def setEnabled(self, enabled):
                self.enabled = enabled
            def setText(self, text):
                self.text = text

        tabs = Tabs()
        controls = [Control() for _ in range(4)]
        logout = Control()
        status = Control()
        _restrict_session_to_logout_retry(tabs, tuple(controls), logout, status)
        self.assertEqual(tabs.enabled, [True, False, False])
        self.assertTrue(all(not control.enabled for control in controls))
        self.assertEqual(logout.text, "Retry sign out")
        self.assertTrue(logout.enabled)
        self.assertTrue(status.enabled)

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
