"""Tests for fail-closed desktop session-expiry cleanup."""
import unittest

from ui.desktop.launch import (
    _close_expired_controller, _close_startup_resources,
    _shutdown_desktop_services, _close_voice_session,
    _cleanup_failed_desktop_initialization,
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
        failed = _close_startup_resources((first, broken, last, first, None))
        self.assertEqual(events, ["first", "last", "broken"])
        self.assertEqual(failed, (broken,))

    def test_partial_startup_cleanup_can_retry_only_failed_resources(self):
        events = []

        class Resource:
            def __init__(self, name, failures=0):
                self.name = name
                self.failures = failures

            def close(self):
                events.append(self.name)
                if self.failures:
                    self.failures -= 1
                    raise OSError("temporary close failure")

        first = Resource("first")
        flaky = Resource("flaky", failures=1)
        remaining = _close_startup_resources((first, flaky))
        self.assertEqual(remaining, (flaky,))
        self.assertEqual(events, ["flaky", "first"])

        remaining = _close_startup_resources(remaining)
        self.assertEqual(remaining, ())
        self.assertEqual(events, ["flaky", "first", "flaky"])

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

    def test_failed_initialization_revokes_session_before_closing_resources(self):
        events = []

        class Sessions:
            def logout(self):
                events.append("revoke")

        class Resource:
            def close(self):
                events.append("close")

        class Lifecycle:
            def close(self, *, before_close):
                before_close()
                Resource().close()

        error = _cleanup_failed_desktop_initialization(Lifecycle(), Sessions())
        self.assertIsNone(error)
        self.assertEqual(events, ["revoke", "close"])

    def test_failed_initialization_closes_partially_composed_controller(self):
        events = []

        class Controller:
            def logout(self):
                events.append("controller-logout")

        class Sessions:
            def logout(self):
                events.append("session-logout")

        class Resource:
            def close(self):
                events.append("resource-close")

        class Lifecycle:
            def close(self, *, before_close):
                before_close()
                Resource().close()

        result = _cleanup_failed_desktop_initialization(
            Lifecycle(), Sessions(), Controller(),
        )
        self.assertIsNone(result)
        self.assertEqual(events, ["controller-logout", "resource-close"])

    def test_failed_initialization_retains_retryable_cleanup_error(self):
        error = OSError("credential store unavailable")
        events = []

        class Sessions:
            def logout(self):
                events.append("revoke")
                raise error

        class Resource:
            def close(self):
                events.append("close")

        class Lifecycle:
            def close(self, *, before_close):
                try:
                    before_close()
                finally:
                    Resource().close()

        result = _cleanup_failed_desktop_initialization(Lifecycle(), Sessions())
        self.assertIs(result, error)
        self.assertEqual(events, ["revoke", "close"])

    def test_missing_lifecycle_does_not_attempt_startup_cleanup(self):
        class Sessions:
            def logout(self):
                raise AssertionError("no services were composed")

        self.assertIsNone(_cleanup_failed_desktop_initialization(None, Sessions()))

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

    def test_disabling_microphone_revokes_and_closes_the_session(self):
        from ui.desktop.launch import _retire_voice_session

        events = []

        class VoiceSession:
            def disable(self):
                events.append("disable")
            def close(self):
                events.append("close")

        class Status:
            def setText(self, value):
                raise AssertionError("successful cleanup must not show a warning")

        failed = _retire_voice_session(VoiceSession(), Status())
        self.assertFalse(failed)
        self.assertEqual(events, ["disable", "close"])

    def test_microphone_close_is_attempted_when_revocation_fails(self):
        from ui.desktop.launch import _retire_voice_session

        events = []

        class VoiceSession:
            def disable(self):
                events.append("disable")
                raise PermissionError("capture interruption failed")
            def close(self):
                events.append("close")

        class Status:
            value = None
            def setText(self, value):
                self.value = value

        status = Status()
        failed = _retire_voice_session(VoiceSession(), status)
        self.assertTrue(failed)
        self.assertEqual(events, ["disable", "close"])
        self.assertEqual(
            status.value, "Warning: microphone interruption failed (PermissionError).",
        )

    def test_microphone_close_failure_is_reported_and_does_not_escape(self):
        class VoiceSession:
            def close(self):
                raise OSError("audio device busy")

        class Status:
            value = None
            def setText(self, value):
                self.value = value

        status = Status()
        error = _close_voice_session(VoiceSession(), status)
        self.assertIsInstance(error, OSError)
        self.assertEqual(
            status.value, "Warning: microphone cleanup failed (OSError).",
        )

    def test_microphone_close_success_returns_no_error(self):
        class VoiceSession:
            closed = False
            def close(self):
                self.closed = True

        class Status:
            def setText(self, value):
                raise AssertionError("successful cleanup must not show a warning")

        session = VoiceSession()
        self.assertIsNone(_close_voice_session(session, Status()))
        self.assertTrue(session.closed)

    def test_missing_microphone_session_is_a_noop(self):
        class Status:
            def setText(self, value):
                raise AssertionError("missing session must not show a warning")

        self.assertIsNone(_close_voice_session(None, Status()))

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


    def test_speech_session_is_revoked_but_not_closed_while_playback_is_active(self):
        from ui.desktop.launch import _retire_speech_session

        events = []

        class Session:
            def disable(self):
                events.append("disable")
            def close(self):
                events.append("close")

        class Status:
            def setText(self, value):
                raise AssertionError("successful revocation must not report a warning")

        failed = _retire_speech_session(
            Session(), playback_active=True, status_label=Status(),
        )
        self.assertFalse(failed)
        self.assertEqual(events, ["disable"])

    def test_speech_session_closes_immediately_when_playback_is_idle(self):
        from ui.desktop.launch import _retire_speech_session

        events = []

        class Session:
            def disable(self):
                events.append("disable")
            def close(self):
                events.append("close")

        class Status:
            def setText(self, value):
                raise AssertionError("successful cleanup must not report a warning")

        failed = _retire_speech_session(
            Session(), playback_active=False, status_label=Status(),
        )
        self.assertFalse(failed)
        self.assertEqual(events, ["disable", "close"])

    def test_retired_speech_backend_close_failure_is_reported(self):
        from ui.desktop.launch import _close_retired_speech_session

        class Session:
            def close(self):
                raise OSError("backend busy")

        class Status:
            value = None
            def setText(self, value):
                self.value = value

        status = Status()
        error = _close_retired_speech_session(Session(), status)
        self.assertIsInstance(error, OSError)
        self.assertEqual(
            status.value, "Warning: retired speech backend cleanup failed (OSError).",
        )

    def test_speech_revocation_failure_does_not_close_active_backend(self):
        from ui.desktop.launch import _retire_speech_session

        events = []

        class Session:
            def disable(self):
                events.append("disable")
                raise PermissionError("cannot interrupt")
            def close(self):
                events.append("close")

        class Status:
            value = None
            def setText(self, value):
                self.value = value

        status = Status()
        failed = _retire_speech_session(
            Session(), playback_active=True, status_label=status,
        )
        self.assertTrue(failed)
        self.assertEqual(events, ["disable"])
        self.assertEqual(
            status.value, "Warning: spoken-reply interruption failed (PermissionError).",
        )


if __name__ == "__main__":
    unittest.main()
