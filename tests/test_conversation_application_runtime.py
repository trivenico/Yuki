import unittest

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_application_runtime import build_conversation_application_runtime
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoTools:
    def authorize_tool(self, name):
        return False


def _agent_loop():
    def model(**kwargs):
        return {"text": "ok", "tool_calls": {"tool_calls": []}}
    return AgentExecutionLoop(model, StructuredToolProtocol(GuardedToolRuntime({}, _NoTools())))


class _Store:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


class ConversationApplicationRuntimeTests(unittest.TestCase):
    def test_composes_service_and_closes_store_once(self):
        store = _Store()
        runtime = build_conversation_application_runtime(
            {}, agent_loop=_agent_loop(), owner_id="local-owner-123",
            store_factory=lambda config: store,
        )
        self.assertIs(runtime.store, store)
        self.assertEqual(runtime.owner_id, "local-owner-123")
        self.assertIsNotNone(runtime.conversations)
        runtime.close()
        runtime.close()
        self.assertEqual(store.close_count, 1)

    def test_api_runtime_can_leave_owner_resolution_to_authenticated_surface(self):
        store = _Store()
        runtime = build_conversation_application_runtime(
            {}, agent_loop=_agent_loop(), store_factory=lambda config: store,
        )
        self.assertIsNone(runtime.owner_id)
        runtime.close()
        self.assertEqual(store.close_count, 1)

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
