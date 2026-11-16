"""Tests for fail-closed desktop session-expiry cleanup."""
import unittest

from ui.desktop.launch import _close_expired_controller, _close_startup_resources


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
