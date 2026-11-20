import json
import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory, MemoryConsentError
from core.native_intents import IntentKind
from core.native_memory_command_adapter import NativeMemoryCommandAdapter


class NativeMemoryCommandAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = CompanionMemory(MemoryStore(Path(self.temp.name) / "owner-memory.json"))
        self.checks = []
        self.adapter = NativeMemoryCommandAdapter(
            lambda actor: self.memory if actor == "owner-1" else CompanionMemory(
                MemoryStore(Path(self.temp.name) / "other-owner.json")
            ),
            authorize_capability=lambda actor, capability: self._authorize(actor, capability),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _authorize(self, actor, capability):
        self.checks.append((actor, capability))
        return actor == "owner-1"

    def test_registers_only_memory_search_and_remember(self):
        self.assertEqual(set(self.adapter.handlers), {
            IntentKind.MEMORY_SEARCH, IntentKind.MEMORY_REMEMBER,
        })

    def test_search_returns_ranked_bounded_matches(self):
        self.memory.enable()
        self.memory.remember("Yuki native architecture", category="note")
        self.memory.remember("native task runtime architecture")
        self.memory.remember("unrelated gardening note")
        output = json.loads(self.adapter.handlers[IntentKind.MEMORY_SEARCH](
            "owner-1", ("native architecture",)
        ))
        self.assertEqual(len(output["matches"]), 2)
        self.assertIn("Yuki", output["matches"][0]["text"])

    def test_remember_requires_companion_memory_consent(self):
        with self.assertRaises(MemoryConsentError):
            self.adapter.handlers[IntentKind.MEMORY_REMEMBER]("owner-1", ("explicit fact",))
        self.memory.enable()
        output = json.loads(self.adapter.handlers[IntentKind.MEMORY_REMEMBER](
            "owner-1", ("explicit fact",)
        ))
        self.assertTrue(output["saved"])
        self.assertEqual(len(self.memory.list_memories()), 1)

    def test_actor_resolver_keeps_profiles_separate(self):
        self.memory.enable()
        self.memory.remember("private owner one fact")
        other = self.adapter._memory("owner-2")
        self.assertEqual(other.list_memories(), [])
        self.assertEqual(self.memory.list_memories()[0]["text"], "private owner one fact")

    def test_capability_gate_fails_closed(self):
        self.assertTrue(self.adapter.authorize_intent("owner-1", IntentKind.MEMORY_SEARCH))
        self.assertFalse(self.adapter.authorize_intent("owner-2", IntentKind.MEMORY_SEARCH))
        self.assertFalse(self.adapter.authorize_intent("owner-1", IntentKind.TASK_LIST))

    def test_capability_exception_fails_closed(self):
        adapter = NativeMemoryCommandAdapter(
            lambda _: self.memory, authorize_capability=lambda *_: (_ for _ in ()).throw(RuntimeError())
        )
        self.assertFalse(adapter.authorize_intent("owner-1", IntentKind.MEMORY_REMEMBER))

    def test_rejects_bad_resolver_result_and_search_bounds(self):
        bad = NativeMemoryCommandAdapter(lambda _: object(), authorize_capability=lambda *_: True)
        with self.assertRaises(TypeError):
            bad.handlers[IntentKind.MEMORY_SEARCH]("owner-1", ("query",))
        with self.assertRaises(ValueError):
            NativeMemoryCommandAdapter(lambda _: self.memory, authorize_capability=lambda *_: True, search_limit=100)

    def test_memory_command_argument_validation(self):
        with self.assertRaises(ValueError):
            self.adapter.handlers[IntentKind.MEMORY_SEARCH]("owner-1", ())
        with self.assertRaises(ValueError):
            self.adapter.handlers[IntentKind.MEMORY_REMEMBER]("owner-1", ("",))


if __name__ == "__main__":
    unittest.main()
