import tempfile
import unittest
from pathlib import Path

from companion.application import CompanionApplicationService
from companion.conversation import ConversationReply
from companion.memory_consent import CompanionMemory
from core.auth import BuiltInAuthService
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity
from ui.desktop.controller import DesktopCompanionController


class FakeKeyring:
    def __init__(self):
        self.values = {}
    def set_password(self, service, account, value):
        self.values[(service, account)] = value
    def get_password(self, service, account):
        return self.values.get((service, account))
    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class FakeProvider:
    def generate_chat(self, system, messages):
        return "Yuki reply"


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = BuiltInAuthService(str(Path(self.temp.name) / "auth.sqlite3"), password_iterations=100_000)
        self.auth.bootstrap_admin("desktop.admin", "A strong desktop password 4!")
        self.sessions = DesktopSessionManager(self.auth, FakeKeyring())
        self.identity = self.sessions.login("desktop.admin", "A strong desktop password 4!")
        self.memory = CompanionMemory()
        self.app = CompanionApplicationService(FakeProvider(), memory=self.memory)
        self.controller = DesktopCompanionController(self.app, self.identity, self.sessions)

    def test_chat_uses_shared_application_service(self):
        self.assertEqual(self.controller.respond("hello"), "Yuki reply")
        self.assertEqual(self.app.session.conversation_turns, 1)

    def test_memory_context_is_explicit_and_revocable(self):
        self.assertFalse(self.controller.memory_context_enabled())
        self.controller.set_memory_context(True)
        self.assertTrue(self.controller.memory_context_enabled())
        self.controller.set_memory_context(False)
        self.assertFalse(self.controller.memory_context_enabled())

    def test_expired_session_blocks_chat_and_memory_changes(self):
        self.auth.revoke_session(self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.controller.respond("hello")
        with self.assertRaises(PermissionError):
            self.controller.set_memory_context(True)

    def test_logout_clears_session_conversation_and_memory_context(self):
        self.controller.set_memory_context(True)
        self.controller.respond("hello")
        self.assertTrue(self.controller.logout())
        self.assertFalse(self.memory.context_enabled)
        self.assertEqual(self.app.session.conversation_turns, 0)
        with self.assertRaises(PermissionError):
            self.controller.respond("after logout")


if __name__ == "__main__":
    unittest.main()
