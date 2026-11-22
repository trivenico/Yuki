"""Regression tests for native-command/logout session serialization."""
from __future__ import annotations

import threading
import unittest

from ui.desktop.shared_desktop_controller import SharedDesktopControllerBridge


class _Chat:
    def __getattr__(self, name):
        if name == "logout":
            return lambda: True
        return lambda *args, **kwargs: None


class _Specialists:
    def __getattr__(self, name):
        if name == "logout":
            return lambda: True
        return lambda *args, **kwargs: None


class _BlockingNative:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def dispatch(self, text, *, confirmed=False):
        self.started.set()
        if not self.release.wait(2):
            raise TimeoutError("test did not release native command")
        return {"executed": True}


class SharedDesktopNativeLogoutTests(unittest.TestCase):
    def test_logout_waits_for_admitted_native_command_before_session_cleanup(self):
        native = _BlockingNative()
        bridge = SharedDesktopControllerBridge(_Chat(), _Specialists(), native_commands=native)
        dispatch_done = threading.Event()
        logout_done = threading.Event()
        failures = []

        def dispatch():
            try:
                bridge.dispatch_native_command("/memory remember approved", confirmed=True)
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                dispatch_done.set()

        def logout():
            try:
                bridge.logout()
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                logout_done.set()

        dispatch_thread = threading.Thread(target=dispatch)
        dispatch_thread.start()
        self.assertTrue(native.started.wait(1), "native command did not start")

        logout_thread = threading.Thread(target=logout)
        logout_thread.start()
        self.assertFalse(
            logout_done.wait(0.05),
            "logout must wait for a native command admitted before the close gate",
        )

        native.release.set()
        dispatch_thread.join(1)
        logout_thread.join(1)
        self.assertFalse(dispatch_thread.is_alive())
        self.assertFalse(logout_thread.is_alive())
        self.assertTrue(dispatch_done.is_set())
        self.assertTrue(logout_done.is_set())
        self.assertEqual(failures, [])

        with self.assertRaisesRegex(RuntimeError, "closed"):
            bridge.dispatch_native_command("/status")


if __name__ == "__main__":
    unittest.main()
