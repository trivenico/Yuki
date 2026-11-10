import tempfile
import unittest
from pathlib import Path

from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.conversation_store import ConversationStore
from core.durable_task_engine import DurableTaskEngine
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

    def test_runtime_injects_provider_catalog_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            catalog = lambda: [{"name": "ollama", "ready": True}]
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), provider_catalog=catalog,
            )
            self.assertIs(runtime.app.provider_catalog, catalog)
            self.assertEqual(runtime.app.provider_catalog()[0]["name"], "ollama")
            runtime.close()

    def test_runtime_injects_provider_selection_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            selector = lambda name: {"name": name}
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), provider_selector=selector,
            )
            self.assertIs(runtime.app.provider_selector, selector)
            runtime.close()

    def test_asgi_lifespan_closes_storage_on_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
            )
            events = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
            sent = []
            async def receive():
                return events.pop(0)
            async def send(message):
                sent.append(message)
            import asyncio
            asyncio.run(runtime({"type": "lifespan"}, receive, send))
            self.assertEqual(sent, [
                {"type": "lifespan.startup.complete"},
                {"type": "lifespan.shutdown.complete"},
            ])
            self.assertTrue(runtime._closed)


    def test_runtime_owns_and_closes_injected_durable_task_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            engine = DurableTaskEngine(root / "durable.sqlite")
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), durable_task_engine=engine,
            )
            self.assertIs(runtime.task_engine, engine)
            self.assertIsNotNone(runtime.app.task_service)
            runtime.close()
            runtime.close()
            with self.assertRaisesRegex(RuntimeError, "closed"):
                engine.create_goal()

    def test_invalid_dependencies_fail_before_store_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TypeError, "auth_service"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=object(), agent_loop=_loop(),
                )


if __name__ == "__main__":
    unittest.main()
