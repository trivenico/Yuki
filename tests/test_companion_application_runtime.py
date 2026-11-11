import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from companion.application_runtime import build_companion_application_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol

class _NoTools:
    def authorize_tool(self, name):
        return False

def _loop():
    def model(**kwargs):
        return {"text": "hello", "tool_calls": {"tool_calls": []}}
    return AgentExecutionLoop(model, StructuredToolProtocol(GuardedToolRuntime({}, _NoTools())))

class CompanionApplicationRuntimeTests(unittest.TestCase):
    def test_builds_shared_conversation_and_local_companion_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                self.assertTrue(runtime.owner_id.startswith("local-"))
                self.assertIsNotNone(runtime.conversations)
                self.assertEqual(runtime.persona.name, "Yuki")
                self.assertEqual(runtime.emotion.tone, "neutral")
                self.assertFalse(runtime.memory_enabled)
                self.assertIsNone(runtime.memory)
                self.assertTrue((root / "companion").is_dir())
                self.assertIn("You are Yuki", runtime.companion_guidance())
            finally:
                runtime.close()
                runtime.close()

    def test_memory_is_only_constructed_after_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = build_companion_application_runtime(
                {"workspace": str(root), "companion_memory_enabled": True},
                agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                self.assertTrue(runtime.memory_enabled)
                saved = runtime.memory.add("Owner-approved note", category="note")
                self.assertEqual(runtime.memory.list()[0]["id"], saved["id"])
                self.assertTrue((root / "companion" / "memory.json").is_file())
            finally:
                runtime.close()

    def test_reminders_and_identity_are_local_and_consent_gated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                with self.assertRaises(PermissionError):
                    runtime.identities.add("Owner", consent=False)
                identity = runtime.identities.add("Owner", consent=True)
                self.assertEqual(runtime.identities.list()[0]["id"], identity["id"])
                reminder = runtime.reminders.add(
                    "Review Yuki", datetime.now(timezone.utc) - timedelta(seconds=1)
                )
                due = runtime.reminders.due()
                self.assertEqual([item["id"] for item in due], [reminder["id"]])
                self.assertEqual(runtime.reminders.due(), [])
            finally:
                runtime.close()

    def test_rejects_invalid_memory_configuration_before_database_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                build_companion_application_runtime(
                    {"workspace": str(root), "companion_memory_enabled": "yes"},
                    agent_loop=_loop(),
                )
            self.assertFalse((root / "conversations.sqlite3").exists())

    def test_requires_explicit_workspace(self):
        with self.assertRaisesRegex(ValueError, "workspace"):
            build_companion_application_runtime({}, agent_loop=_loop())

if __name__ == "__main__":
    unittest.main()
