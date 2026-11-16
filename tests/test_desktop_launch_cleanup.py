"""Tests for fail-closed desktop session-expiry cleanup."""
import unittest

from ui.desktop.launch import _close_expired_controller


class Controller:
    def __init__(self, error=None):
        self.error = error
        self.logout_calls = 0

    def logout(self):
        self.logout_calls += 1
        if self.error is not None:
            raise self.error


class DesktopLaunchCleanupTests(unittest.TestCase):
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
