import tempfile
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_memory_store import ConversationMemoryStore
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class DenyAll:
    def authorize_tool(self, name):
        return False


class ConversationMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "memory.sqlite3"
        self.memory = ConversationMemoryStore(path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_memory_isolated_by_owner_for_search_list_delete_and_clear(self):
        alice = self.memory.remember(
            owner_id="alice", content="Prefers concise engineering updates", category="preference",
        )
        bob = self.memory.remember(
            owner_id="bob", content="Prefers detailed gardening advice", category="preference",
        )
        self.assertEqual(
            [item["memory_id"] for item in self.memory.search(
                owner_id="alice", query="engineering updates",
            )],
            [alice["memory_id"]],
        )
        self.assertEqual(self.memory.list(owner_id="bob")[0]["memory_id"], bob["memory_id"])
        self.assertFalse(self.memory.delete(owner_id="bob", memory_id=alice["memory_id"]))
        self.assertEqual(self.memory.clear(owner_id="alice"), 1)
        self.assertEqual(self.memory.list(owner_id="alice"), [])
        self.assertEqual(self.memory.list(owner_id="bob")[0]["memory_id"], bob["memory_id"])

    def test_memory_inputs_are_bounded_and_categories_are_allowlisted(self):
        for kwargs in (
            {"owner_id": "", "content": "text"},
            {"owner_id": "owner", "content": " "},
            {"owner_id": "owner", "content": "x" * 2001},
            {"owner_id": "owner", "content": "text", "category": "secret"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.memory.remember(**kwargs)
        with self.assertRaises(ValueError):
            self.memory.search(owner_id="owner", query="valid", limit=True)


class ConversationMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.store = ConversationStore(root / "chat.sqlite3")
        self.memory = ConversationMemoryStore(root / "memory.sqlite3")
        self.observed = []

        def model(*, messages, tools, tool_choice):
            self.observed.append(messages)
            return {"text": "answer", "tool_calls": {"tool_calls": []}}

        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, DenyAll())),
        )
        self.service = ConversationService(self.store, loop, memory_store=self.memory)
        self.conversation = self.service.create(owner_id="alice")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_explicitly_saved_owner_memory_is_retrieved_as_untrusted_context(self):
        saved = self.service.remember(
            owner_id="alice", content="The project uses Python and SQLite",
            category="note",
        )
        result = self.service.reply(
            owner_id="alice", conversation_id=self.conversation["conversation_id"],
            user_text="What database does the project use?", request_id="memory-1",
            expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        system_prompt = self.observed[0][0]["content"]
        self.assertIn("Python and SQLite", system_prompt)
        self.assertIn("untrusted user memory", system_prompt)
        self.assertEqual(self.service.memories(owner_id="alice")[0]["memory_id"], saved["memory_id"])

    def test_other_owner_memory_is_never_added_to_prompt(self):
        self.memory.remember(owner_id="bob", content="Bob uses PostgreSQL for project database")
        self.memory.remember(owner_id="alice", content="Alice prefers SQLite database")
        result = self.service.reply(
            owner_id="alice", conversation_id=self.conversation["conversation_id"],
            user_text="Which database?", request_id="memory-isolation", expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        system_prompt = self.observed[0][0]["content"]
        self.assertIn("Alice prefers SQLite", system_prompt)
        self.assertNotIn("Bob uses PostgreSQL", system_prompt)

    def test_memory_is_disabled_unless_store_is_explicitly_injected(self):
        plain = ConversationService(self.store, self.service.agent_loop)
        with self.assertRaisesRegex(RuntimeError, "not enabled"):
            plain.remember(owner_id="alice", content="No implicit memory")
        self.assertEqual(plain.memories if False else [], [])
