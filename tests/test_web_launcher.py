import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.runtime import ConversationRuntime
from scripts import run_web


class _Provider:
    def generate_chat(self, *, messages, tools, tool_choice="auto"):
        return {"text": "test response", "tool_calls": {"tool_calls": []}}

    def generate_chat_stream(self, *, messages, tools, tool_choice="auto"):
        yield {"type": "text_delta", "text": "test response"}


class WebLauncherTests(unittest.TestCase):
    def test_create_app_composes_provider_auth_and_local_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(run_web, "load_config", return_value={
                "workspace": directory, "storage_backend": "sqlite",
                "provider": "openai", "sandbox_jobs_enabled": False,
            }), patch.object(run_web, "create_provider", return_value=_Provider()), patch.object(
                run_web, "build_production_conversation_runtime",
                wraps=run_web.build_production_conversation_runtime,
            ) as production_factory:
                app = run_web.create_app()
            production_factory.assert_called_once()
            self.assertFalse(production_factory.call_args.args[0]["sandbox_jobs_enabled"])
            self.assertIsInstance(app, ConversationRuntime)
            self.assertTrue((Path(directory) / "auth.sqlite3").exists())
            self.assertTrue(callable(app.app.provider_selector))
            self.assertTrue(callable(app.app.provider_catalog))
            app.close()

    def test_default_tool_policy_denies_all_tools(self):
        self.assertFalse(run_web.DenyAllTools.authorize_tool("anything"))


if __name__ == "__main__":
    unittest.main()
