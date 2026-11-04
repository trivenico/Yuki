import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.runtime import ConversationRuntime
from scripts import run_web
from providers.reasoning_runtime import memory_actor_id_for_owner


class _Provider:
    def __init__(self):
        self.messages = None

    def generate_chat(self, *, messages, tools, tool_choice="auto"):
        self.messages = messages
        return {"text": "test response", "tool_calls": {"tool_calls": []}}

    def generate_chat_stream(self, *, messages, tools, tool_choice="auto"):
        yield {"type": "text_delta", "text": "test response"}


class WebLauncherTests(unittest.TestCase):
    def test_create_app_composes_provider_auth_and_local_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(run_web, "load_config", return_value={
                "workspace": directory, "storage_backend": "sqlite",
                "provider": "openai",
            }), patch.object(run_web, "create_provider", return_value=(provider := _Provider())):
                app = run_web.create_app()
            self.assertIsInstance(app, ConversationRuntime)
            self.assertTrue((Path(directory) / "auth.sqlite3").exists())
            self.assertTrue(callable(app.app.provider_selector))
            self.assertTrue(callable(app.app.provider_catalog))
            self.assertTrue(callable(app.app.conversations.agent_loop_factory))
            self.assertTrue((Path(directory) / "agent_memory.sqlite3").exists())
            owner_loop = app.app.conversations.agent_loop_factory("authenticated-owner")
            self.assertTrue(owner_loop.model.actor_id.startswith("owner-"))
            app.app.memory_store.remember(
                actor_id=memory_actor_id_for_owner("authenticated-owner"),
                agent_id="assistant", content="My owner-entered private note.",
                source="owner_web_ui", verified=False,
            )
            result = owner_loop.run("private note", tools=[])
            self.assertEqual(result.status, "completed")
            self.assertIn("My owner-entered private note.", provider.messages[-1]["content"])
            self.assertIn("unverified_reference_records", provider.messages[-1]["content"])
            app.close()

    def test_default_tool_policy_denies_all_tools(self):
        self.assertFalse(run_web.DenyAllTools.authorize_tool("anything"))


if __name__ == "__main__":
    unittest.main()
