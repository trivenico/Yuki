import tempfile
import unittest
from unittest.mock import patch
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

    def test_close_is_attempted_once_even_when_store_close_fails(self):
        class FailingStore:
            def __init__(self):
                self.close_calls = 0
            def close(self):
                self.close_calls += 1
                raise OSError("storage close failed")

        store = FailingStore()
        runtime = build_conversation_application_runtime(
            {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
        )
        with self.assertRaisesRegex(OSError, "storage close failed"):
            runtime.close()
        runtime.close()
        self.assertTrue(runtime.closed)
        self.assertEqual(store.close_calls, 1)

    def test_store_cleanup_failure_does_not_mask_service_construction_error(self):
        class FailingStore:
            def __init__(self):
                self.close_calls = 0
            def close(self):
                self.close_calls += 1
                raise OSError("cleanup failed")

        store = FailingStore()
        with patch(
            "core.conversation_application_runtime.ConversationService",
            side_effect=ValueError("service construction failed"),
        ):
            with self.assertRaisesRegex(ValueError, "service construction failed"):
                build_conversation_application_runtime(
                    {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
                )
        self.assertEqual(store.close_calls, 1)

    def test_api_runtime_can_leave_owner_resolution_to_authenticated_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "api.sqlite3")
            runtime = build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
            )
            self.assertIsNone(runtime.owner_id)
            runtime.close()

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
