import json
import os
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
                "provider": "openai",
            }), patch.dict(os.environ, {"YUKI_ALLOW_CLOUD": "true"}), patch.object(run_web.ProviderManager, "create_selected_runtime_adapter", return_value=_Provider()):
                app = run_web.create_app()
            self.assertIsInstance(app, ConversationRuntime)
            self.assertTrue((Path(directory) / "auth.sqlite3").exists())
            self.assertTrue(callable(app.app.provider_selector))
            self.assertTrue(callable(app.app.provider_catalog))
            app.close()


    def test_provider_selection_persistence_round_trips_exact_model_and_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            run_web._persist_provider_selection(
                path, provider="openai", model="gpt-4.1-mini", cloud_authorized=True,
            )
            self.assertEqual(run_web._load_provider_selection(path), {
                "schema_version": 2, "provider": "openai",
                "model": "gpt-4.1-mini", "cloud_authorized": True,
            })
            if os.name != "nt":
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_provider_selection_persistence_rejects_unknown_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            path.write_text(json.dumps({"schema_version": 1, "provider": "ollama"}))
            with self.assertRaisesRegex(RuntimeError, "unsupported schema"):
                run_web._load_provider_selection(path)

    def test_provider_selection_persistence_never_accepts_invalid_consent_type(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-selection.json"
            with self.assertRaisesRegex(ValueError, "Invalid provider selection"):
                run_web._persist_provider_selection(
                    path, provider="openai", model="gpt-4.1-mini", cloud_authorized=1,
                )


    def test_provider_selection_persistence_rejects_symlinks(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}")
            link = root / "provider-selection.json"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            with self.assertRaisesRegex(RuntimeError, "non-symlink"):
                run_web._load_provider_selection(link)

    def test_default_tool_policy_denies_all_tools(self):
        self.assertFalse(run_web.DenyAllTools.authorize_tool("anything"))


if __name__ == "__main__":
    unittest.main()
