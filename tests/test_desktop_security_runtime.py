import unittest
from types import SimpleNamespace

from ui.desktop.security_runtime import create_desktop_security_service


class FakeSessions:
    def __init__(self):
        self.identity = SimpleNamespace(session_id="session-a")

    def current_identity(self):
        return self.identity


class DesktopSecurityRuntimeTests(unittest.TestCase):
    def test_service_is_bound_to_the_exact_authenticated_session(self):
        sessions = FakeSessions()
        service = create_desktop_security_service(sessions, "session-a")
        self.assertFalse(service.active)
        self.assertFalse(service.configured)
        service.start(consent=True)
        self.assertTrue(service.active)
        sessions.identity = SimpleNamespace(session_id="session-b")
        with self.assertRaises(Exception):
            service.snapshot()
        self.assertFalse(service.active)
        service.close()

    def test_expired_or_missing_identity_fails_closed(self):
        sessions = FakeSessions()
        service = create_desktop_security_service(sessions, "session-a")
        sessions.identity = None
        with self.assertRaises(Exception):
            service.start(consent=True)
        self.assertFalse(service.configured)
        service.close()

    def test_monitor_factory_is_lazy_and_service_is_read_only(self):
        sessions = FakeSessions()
        service = create_desktop_security_service(sessions, "session-a")
        self.assertFalse(service.configured)
        service.start(consent=True)
        result = service.snapshot()
        self.assertTrue(result["read_only"])
        self.assertFalse(result["persisted"])
        self.assertFalse(result["automated_response"])
        service.close()


if __name__ == "__main__":
    unittest.main()
