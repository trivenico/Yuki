import io
import tempfile
import unittest
from pathlib import Path

from automation.conversation_cli import run_interactive_chat
from automation.agent_sessions import AgentSessionStore
from automation.conversation_service import ConversationService


class Provider:
    def is_available(self): return True
    def stream(self, prompt):
        yield "Hello "
        yield "from Yuki."


class Manager:
    def __init__(self): self.calls = []
    def create_routed_adapter(self, **kwargs):
        self.calls.append(kwargs)
        return Provider()


class ConversationCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "private" / "chat.sqlite3"

    def test_cli_streams_and_persists_conversation(self):
        manager, output = Manager(), io.StringIO()
        answers = iter(["Hello Yuki", "/back"])
        session_id = run_interactive_chat(
            manager, database_path=self.db, input_fn=lambda _prompt: next(answers),
            output=output,
        )
        self.assertIn("Hello from Yuki.", output.getvalue())
        self.assertEqual(manager.calls[0]["allow_cloud"], False)
        store = AgentSessionStore(self.db, authorize=lambda actor, action, resource: actor == "yuki-local-cli")
        history = ConversationService(store).history(actor_id="yuki-local-cli", session_id=session_id)
        self.assertTrue(any(event.kind == "message.user" for event in history))
        self.assertTrue(any(event.kind == "message.assistant" for event in history))

    def test_cli_resumes_existing_active_session(self):
        manager, output = Manager(), io.StringIO()
        answers = iter(["/back"])
        first = run_interactive_chat(manager, database_path=self.db,
            input_fn=lambda _prompt: next(answers), output=output)
        output2 = io.StringIO()
        second = run_interactive_chat(manager, database_path=self.db,
            input_fn=lambda _prompt: "/back", output=output2)
        self.assertEqual(first, second)
        self.assertIn("Resuming conversation", output2.getvalue())

    def test_cloud_routing_is_disabled_by_default(self):
        manager = Manager()
        run_interactive_chat(manager, database_path=self.db,
            input_fn=lambda _prompt: "/back", output=io.StringIO())
        self.assertFalse(manager.calls)

    def test_rejects_non_boolean_cloud_setting(self):
        with self.assertRaises(TypeError):
            run_interactive_chat(Manager(), database_path=self.db, allow_cloud="yes")


if __name__ == "__main__": unittest.main()
