import unittest

from ui.desktop.lifecycle import DesktopServiceLifecycle, DesktopShutdownError
from providers.hybrid import HybridProvider


class Resource:
    def __init__(self, name, events, error=None):
        self.name = name
        self.events = events
        self.error = error

    def close(self):
        self.events.append(self.name)
        if self.error:
            raise self.error


class DesktopServiceLifecycleTests(unittest.TestCase):
    def test_failed_desktop_composition_revokes_issued_session_without_controller(self):
        from ui.desktop.launch import _close_expired_controller

        identity = object()

        class Sessions:
            def __init__(self):
                self.revoked = []
            def logout(self, *, expected_identity=None):
                self.revoked.append(expected_identity)
                return True

        sessions = Sessions()
        self.assertIsNone(_close_expired_controller(
            None, sessions=sessions, expected_identity=identity,
        ))
        self.assertEqual(sessions.revoked, [identity])

    def test_worker_result_fence_rejects_stale_session_and_shutdown_callbacks(self):
        from ui.desktop.launch import _is_current_session_callback

        old_controller = object()
        new_controller = object()
        self.assertTrue(_is_current_session_callback(
            old_controller, old_controller, 4, 4,
            logout_started=False, shutdown_started=False,
        ))
        self.assertFalse(_is_current_session_callback(
            old_controller, new_controller, 4, 5,
            logout_started=False, shutdown_started=False,
        ))
        self.assertFalse(_is_current_session_callback(
            old_controller, old_controller, 4, 4,
            logout_started=True, shutdown_started=False,
        ))
        self.assertFalse(_is_current_session_callback(
            old_controller, old_controller, 4, 4,
            logout_started=False, shutdown_started=True,
        ))


    def test_stale_voice_completion_is_rejected_before_ui_mutation(self):
        from ui.desktop.launch import _is_current_voice_callback

        old_session = object()
        new_session = object()
        self.assertTrue(_is_current_voice_callback(old_session, old_session, True))
        self.assertFalse(_is_current_voice_callback(old_session, new_session, True))
        self.assertFalse(_is_current_voice_callback(old_session, old_session, False))

    def test_closes_in_reverse_order_and_deduplicates_shared_resources(self):
        events = []
        first = Resource("first", events)
        second = Resource("second", events)
        lifecycle = DesktopServiceLifecycle((first, second, first))

        lifecycle.close()
        lifecycle.close()

        self.assertEqual(events, ["first", "second"])
        self.assertTrue(lifecycle.closed)

    def test_cleanup_continues_after_a_resource_fails(self):
        events = []
        first = Resource("first", events)
        broken = Resource("broken", events, RuntimeError("close failed"))
        last = Resource("last", events)
        lifecycle = DesktopServiceLifecycle((first, broken, last))

        with self.assertRaises(DesktopShutdownError):
            lifecycle.close()

        self.assertEqual(events, ["last", "broken", "first"])
        self.assertFalse(lifecycle.closed)

        # A transient failure remains retryable; successful resources are not
        # closed again when the failed hook succeeds on a later attempt.
        broken.error = None
        lifecycle.close()
        self.assertTrue(lifecycle.closed)
        self.assertEqual(events, ["last", "broken", "first", "broken"])

    def test_desktop_lifecycle_closes_hybrid_provider_after_dependents_once(self):
        events = []

        class Child:
            def __init__(self, name):
                self.name = name
            def generate(self, prompt):
                return prompt
            def is_available(self):
                return True
            def close(self):
                events.append(self.name)
        class Dependent:
            def close(self):
                events.append("dependent")
        provider = HybridProvider(Child("local"), Child("cloud"), owns_providers=True)
        lifecycle = DesktopServiceLifecycle((provider, Dependent()))
        lifecycle.close()
        lifecycle.close()
        self.assertEqual(events, ["dependent", "cloud", "local"])


    def test_failed_resource_close_is_retried_without_reclosing_successful_resources(self):
        events = []
        class RetryResource:
            def __init__(self):
                self.attempts = 0
            def close(self):
                self.attempts += 1
                events.append("retry")
                if self.attempts == 1:
                    raise OSError("temporary close failure")
        stable = Resource("stable", events)
        retry = RetryResource()
        lifecycle = DesktopServiceLifecycle((stable, retry))
        with self.assertRaises(DesktopShutdownError):
            lifecycle.close()
        self.assertFalse(lifecycle.closed)
        lifecycle.close()
        self.assertTrue(lifecycle.closed)
        self.assertEqual(events, ["retry", "stable", "retry"])

    def test_non_closable_resources_are_ignored(self):
        events = []
        lifecycle = DesktopServiceLifecycle((object(), Resource("closable", events)))
        lifecycle.close()
        self.assertEqual(events, ["closable"])

    def test_session_revocation_runs_before_resources_and_failure_does_not_skip_cleanup(self):
        events = []
        resource = Resource("resource", events)
        lifecycle = DesktopServiceLifecycle((resource,))

        def revoke():
            events.append("revoke")
            raise PermissionError("session revoke failed")

        with self.assertRaises(DesktopShutdownError):
            lifecycle.close(before_close=revoke)

        self.assertEqual(events, ["revoke", "resource"])
        self.assertFalse(lifecycle.closed)

        attempts = 0
        def revoke_on_retry():
            nonlocal attempts
            attempts += 1
            events.append("revoke-retry")

        lifecycle.close(before_close=revoke_on_retry)
        self.assertEqual(events, ["revoke", "resource", "revoke-retry"])
        self.assertEqual(attempts, 1)
        self.assertTrue(lifecycle.closed)


if __name__ == "__main__":
    unittest.main()
