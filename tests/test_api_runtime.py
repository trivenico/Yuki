import tempfile
import unittest
from pathlib import Path

from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "Runtime ready", "tool_calls": {"tool_calls": []}}
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
    return AgentExecutionLoop(model, protocol)


class ConversationRuntimeTests(unittest.TestCase):
    def test_runtime_uses_configured_store_and_closes_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth,
                agent_loop=_loop(),
            )
            self.assertIsInstance(runtime.store, ConversationStore)
            conversation = runtime.app.conversations.create(owner_id="runtime-user")
            self.assertEqual(conversation["owner_id"], "runtime-user")
            runtime.close()
            runtime.close()
            self.assertTrue(runtime._closed)

    def test_invalid_dependencies_fail_before_store_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TypeError, "auth_service"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=object(), agent_loop=_loop(),
                )


if __name__ == "__main__":
    unittest.main()
