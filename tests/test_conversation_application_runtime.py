import tempfile
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_application_runtime import build_conversation_application_runtime
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoTools:
    def authorize_tool(self, name):
        return False


def _agent_loop():
    def model(**kwargs):
        return {"text": "ok", "tool_calls": {"tool_calls": []}}
    return AgentExecutionLoop(model, StructuredToolProtocol(GuardedToolRuntime({}, _NoTools())))


class ConversationApplicationRuntimeTests(unittest.TestCase):
    def test_composes_service_and_shutdown_is_safe_to_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversations.sqlite3")
            runtime = build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), owner_id="local-owner-123",
                store_factory=lambda config: store,
            )
            self.assertIs(runtime.store, store)
            self.assertEqual(runtime.owner_id, "local-owner-123")
            self.assertIsNotNone(runtime.conversations)
            runtime.close()
            runtime.close()
            # ConversationStore uses per-operation connections; closing the
            # application runtime must not invalidate subsequent store objects.
            reopened = ConversationStore(Path(directory) / "conversations.sqlite3")
            self.assertEqual(reopened.list_conversations(owner_id="local-owner-123"), [])

    def test_api_runtime_can_leave_owner_resolution_to_authenticated_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "api.sqlite3")
            runtime = build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
            )
            self.assertIsNone(runtime.owner_id)
            runtime.close()

    def test_passes_owner_loop_factory_to_conversation_service(self):
        factory = lambda owner_id: _agent_loop()
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "owner-loops.sqlite3")
            runtime = build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), agent_loop_for_owner=factory,
                store_factory=lambda config: store,
            )
            self.assertIs(runtime.conversations.agent_loop_for_owner, factory)
            runtime.close()

    def test_rejects_invalid_owner_loop_factory_before_opening_store(self):
        opened = []
        with self.assertRaisesRegex(TypeError, "agent_loop_for_owner"):
            build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), agent_loop_for_owner=object(),
                store_factory=lambda config: opened.append(True),
            )
        self.assertEqual(opened, [])

    def test_rejects_invalid_owner_scope_before_opening_store(self):
        opened = []
        with self.assertRaisesRegex(ValueError, "owner_id"):
            build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), owner_id=" ",
                store_factory=lambda config: opened.append(True),
            )
        self.assertEqual(opened, [])

    def test_rejects_invalid_dependencies_before_opening_store(self):
        opened = []
        with self.assertRaisesRegex(TypeError, "AgentExecutionLoop"):
            build_conversation_application_runtime(
                {}, agent_loop=object(), store_factory=lambda config: opened.append(True),
            )
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()
