"""Security tests for the OS-keyring-backed desktop session manager."""
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread, current_thread
from unittest.mock import patch

from core.auth import AuthenticationError, BuiltInAuthService
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.bootstrap import DesktopStartupError


PASSWORD = "A sufficiently long desktop password 7!"


class RecordingAuth(BuiltInAuthService):
    def __init__(self, path):
        super().__init__(path, password_iterations=100_000)
        self.last_issued = None

    def authenticate(self, username, password):
        self.last_issued = super().authenticate(username, password)
        return self.last_issued


class FakeKeyring:
    def __init__(self):
        self.values = {}
        self.fail_set = False
        self.fail_get = False
        self.fail_delete = False
        self.corrupt_after_set = False

    def set_password(self, service, account, token):
        if self.fail_set:
            raise RuntimeError("keyring write failed")
        self.values[(service, account)] = "corrupt" if self.corrupt_after_set else token

    def get_password(self, service, account):
        if self.fail_get:
            raise RuntimeError("keyring read failed")
        return self.values.get((service, account))

    def delete_password(self, service, account):
        if self.fail_delete:
            raise RuntimeError("keyring delete failed")
        self.values.pop((service, account), None)


class DesktopSessionManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = RecordingAuth(str(Path(self.temp.name) / "auth.sqlite3"))
        self.user = self.auth.create_account("desktop.owner", PASSWORD)
        self.keyring = FakeKeyring()
        self.sessions = DesktopSessionManager(self.auth, self.keyring)

    def test_login_persists_only_session_in_keyring_and_resolves_identity(self):
        identity = self.sessions.login("desktop.owner", PASSWORD)
        self.assertEqual(identity.actor_id, self.user.user_id)
        self.assertEqual(identity.session_id, self.auth.last_issued.token)
        self.assertEqual(self.sessions.current_identity(), identity)
        self.assertEqual(
            self.keyring.get_password("yuki.desktop", "active-session"),
            identity.session_id,
        )

    def test_logout_revokes_session_and_removes_keyring_entry(self):
        identity = self.sessions.login("desktop.owner", PASSWORD)
        self.assertTrue(self.sessions.logout())
        self.assertIsNone(self.sessions.current_identity())
        self.assertIsNone(self.keyring.get_password("yuki.desktop", "active-session"))
        self.assertIsNone(self.auth.resolve_session(identity.session_id))

    def test_concurrent_login_keeps_in_memory_owner_aligned_with_keyring(self):
        original_file_lock = __import__("ui.desktop.auth", fromlist=["file_lock"]).file_lock
        first_released_lock = Event()
        second_identity_published = Event()

        @contextmanager
        def controlled_lock(path):
            with original_file_lock(path):
                yield
            # Pause the first login after releasing the shared keyring lock.
            # The second login then completes before the first can return.
            if current_thread().name == "first-login":
                first_released_lock.set()
                if not second_identity_published.wait(timeout=10):
                    raise AssertionError("second login did not publish its identity")

        class ObservedManager(DesktopSessionManager):
            def __setattr__(self, name, value):
                super().__setattr__(name, value)
                if (
                    name == "_last_identity"
                    and value is not None
                    and current_thread().name == "second-login"
                ):
                    second_identity_published.set()

        manager = ObservedManager(self.auth, self.keyring)
        results = {}
        failures = []

        def login(label):
            try:
                results[label] = manager.login("desktop.owner", PASSWORD)
            except Exception as exc:
                failures.append(exc)

        with patch("ui.desktop.auth.file_lock", controlled_lock):
            first = Thread(target=login, args=("first",), name="first-login")
            second = Thread(target=login, args=("second",), name="second-login")
            first.start()
            self.assertTrue(first_released_lock.wait(timeout=10))
            second.start()
            first.join(timeout=10)
            second.join(timeout=10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        active_token = self.keyring.get_password("yuki.desktop", "active-session")
        active_identity = next(
            identity for identity in results.values()
            if identity.session_id == active_token
        )
        self.assertTrue(manager.logout())
        self.assertIsNone(self.auth.resolve_session(active_identity.session_id))
        self.assertIsNone(self.keyring.get_password("yuki.desktop", "active-session"))

    def test_stale_controller_logout_does_not_revoke_newer_session(self):
        first = self.sessions.login("desktop.owner", PASSWORD)
        second_manager = DesktopSessionManager(self.auth, self.keyring)
        second = second_manager.login("desktop.owner", PASSWORD)
        self.assertNotEqual(first.session_id, second.session_id)

        # A stale controller in another manager/process must not revoke the
        # newer token occupying the shared OS-keyring slot.
        self.assertFalse(self.sessions.logout(expected_identity=first))
        self.assertEqual(second_manager.current_identity(), second)
        self.assertIsNotNone(self.auth.resolve_session(second.session_id))

        self.assertTrue(second_manager.logout(expected_identity=second))
        self.assertIsNone(self.sessions.current_identity())

    def test_stale_logout_preserves_newer_identity_captured_by_same_manager(self):
        first = self.sessions.login("desktop.owner", PASSWORD)
        second = self.sessions.login("desktop.owner", PASSWORD)
        self.assertFalse(self.sessions.logout(expected_identity=first))
        self.assertEqual(self.sessions.current_identity(), second)
        self.assertTrue(self.sessions.logout())
        self.assertIsNone(self.sessions.current_identity())

    def test_manager_without_captured_session_cannot_revoke_another_process_login(self):
        observer = DesktopSessionManager(self.auth, self.keyring)
        self.assertIsNone(observer.current_identity())

        owner = DesktopSessionManager(self.auth, self.keyring)
        identity = owner.login("desktop.owner", PASSWORD)

        # Closing an instance that never captured this session must be a no-op.
        self.assertFalse(observer.logout())
        self.assertEqual(owner.current_identity(), identity)
        self.assertIsNotNone(self.auth.resolve_session(identity.session_id))

    def test_invalid_credentials_do_not_write_to_keyring(self):
        with self.assertRaises(AuthenticationError):
            self.sessions.login("desktop.owner", "incorrect password")
        self.assertEqual(self.keyring.values, {})

    def test_keyring_write_failure_revokes_newly_issued_session(self):
        self.keyring.fail_set = True
        with self.assertRaises(DesktopStartupError):
            self.sessions.login("desktop.owner", PASSWORD)
        self.assertIsNone(self.auth.resolve_session(self.auth.last_issued.token))

    def test_keyring_readback_mismatch_revokes_newly_issued_session(self):
        self.keyring.corrupt_after_set = True
        with self.assertRaises(DesktopStartupError):
            self.sessions.login("desktop.owner", PASSWORD)
        self.assertIsNone(self.auth.resolve_session(self.auth.last_issued.token))

    def test_keyring_read_failure_fails_closed(self):
        self.keyring.fail_get = True
        with self.assertRaises(DesktopStartupError):
            self.sessions.current_identity()

    def test_revoked_session_is_not_returned_as_current_identity(self):
        identity = self.sessions.login("desktop.owner", PASSWORD)
        self.assertTrue(self.auth.revoke_session(identity.session_id))
        self.assertIsNone(self.sessions.current_identity())

    def test_logout_keyring_delete_failure_reports_cleanup_error_after_revocation(self):
        identity = self.sessions.login("desktop.owner", PASSWORD)
        self.keyring.fail_delete = True
        with self.assertRaises(DesktopStartupError):
            self.sessions.logout()
        self.assertIsNone(self.auth.resolve_session(identity.session_id))
        # The stale token may remain in the OS keyring, but it is revoked and
        # therefore cannot authenticate a subsequent desktop operation.


if __name__ == "__main__":
    unittest.main()
