import tempfile
import unittest
from pathlib import Path

from companion.application import CompanionApplicationService
from companion.conversation import ConversationError
from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory


class FakeProvider:
    def __init__(self, answers=None):
        self.answers = list(answers or ["hello", "second"])
        self.prompts = []

    def is_available(self):
        return True

    def generate_chat(self, system, messages):
        self.prompts.append((system, messages))
        return self.answers.pop(0)


class CompanionApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provider = FakeProvider()
        self.memory = CompanionMemory(MemoryStore(str(Path(self.temp.name) / "memory.json")))
        self.app = CompanionApplicationService(self.provider, memory=self.memory)

    def test_memory_is_not_injected_until_session_context_consent(self):
        self.memory.enable()
        saved = self.memory.remember("I prefer concise replies", category="preference")
        self.app.respond("hello there")
        self.assertNotIn("I prefer concise replies", self.provider.prompts[0][0])
        self.memory.enable_context()
        self.app.respond("Please use concise replies")
        self.assertIn("I prefer concise replies", self.provider.prompts[1][0])

    def test_memory_retrieval_failure_fails_closed_before_provider_request(self):
        class BrokenMemory(CompanionMemory):
            def context_entries(self, *, query="", limit=10):
                raise OSError("private database details")

        memory = BrokenMemory(MemoryStore(str(Path(self.temp.name) / "broken-memory.json")))
        app = CompanionApplicationService(self.provider, memory=memory)
        with self.assertRaises(ConversationError):
            app.respond("hello")
        self.assertEqual(self.provider.prompts, [])

    def test_role_and_reset_are_shared_service_operations(self):
        self.app.set_role("developer")
        self.app.respond("review this")
        self.assertIn("Current interaction role: developer", self.provider.prompts[0][0])
        self.app.reset_conversation()
        self.assertEqual(self.app.session.history(), ())

    def test_service_rejects_mismatched_provider_session(self):
        from companion.conversation import ConversationSession
        other = FakeProvider()
        with self.assertRaises(ValueError):
            CompanionApplicationService(
                self.provider, memory=self.memory, session=ConversationSession(other),
            )


if __name__ == "__main__":
    unittest.main()
