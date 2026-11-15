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

    def test_store_close_hook_is_invoked_exactly_once(self):
        calls = []

        class Store(ConversationStore):
            def close(self):
                calls.append("closed")

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "close-once.sqlite3")
            runtime = build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
            )
            runtime.close()
            runtime.close()
        self.assertEqual(calls, ["closed"])

    def test_failing_store_close_hook_is_not_retried(self):
        calls = []

        class Store(ConversationStore):
            def close(self):
                calls.append("close")
                raise RuntimeError("close failed")

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "close-failure.sqlite3")
            runtime = build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
            )
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                runtime.close()
            runtime.close()
        self.assertEqual(calls, ["close"])

    def test_owned_cleanup_hooks_run_after_store_even_when_store_close_fails(self):
        calls = []

        class Store(ConversationStore):
            def close(self):
                calls.append("store")
                raise RuntimeError("store close failed")

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "owned-resources.sqlite3")
            runtime = build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
                close_callbacks=(lambda: calls.append("provider"),),
            )
            with self.assertRaisesRegex(RuntimeError, "store close failed"):
                runtime.close()
            runtime.close()
        self.assertEqual(calls, ["store", "provider"])

    def test_invalid_cleanup_hooks_fail_before_opening_store(self):
        opened = []
        with self.assertRaisesRegex(TypeError, "close_callbacks"):
            build_conversation_application_runtime(
                {}, agent_loop=_agent_loop(), close_callbacks=(object(),),
                store_factory=lambda config: opened.append(True),
            )
        self.assertEqual(opened, [])

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
