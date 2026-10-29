import tempfile
import unittest
from pathlib import Path

import yaml

from core.settings_store import read_user_settings, save_user_settings
from core.model_catalog import list_providers


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.yaml"
        self.path.write_text(
            "provider: ollama\ncustom_setting: keep-me\n", encoding="utf-8"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_settings_provider_options_match_shared_runtime_catalog(self):
        supported = {item["id"]: item for item in list_providers(supported_only=True)}
        self.assertEqual(set(PROVIDERS), set(supported))
        self.assertEqual(set(MODEL_SETTING_BY_PROVIDER), {
            provider for provider, item in supported.items() if item["model_config"] is not None
        })
        self.assertNotIn("gemini_api", PROVIDERS)
        self.assertNotIn("nvidia", PROVIDERS)

    def test_safe_defaults(self):
        settings = read_user_settings(self.path)
        self.assertEqual(settings["provider"], "ollama")
        self.assertEqual(settings["ollama_model"], "llama3.2:3b")
        self.assertEqual(settings["openai_model"], "gpt-4.1-mini")
        self.assertEqual(settings["anthropic_model"], "")
        self.assertFalse(settings["voice_enabled"])
        self.assertTrue(settings["firewall_dry_run"])
        self.assertFalse(settings["background_evolution_enabled"])
        self.assertTrue(settings["agent_council_enabled"])
        self.assertFalse(settings["screen_observation_enabled"])
        self.assertFalse(settings["camera_access_enabled"])
        self.assertFalse(settings["desktop_control_enabled"])

    def test_allowlisted_update_preserves_unrelated_settings(self):
        saved = save_user_settings(
            {
                "provider": "openai",
                "ollama_model": "qwen2.5:3b",
                "openai_model": "gpt-4.1-mini",
                "voice_enabled": True,
                "background_evolution_enabled": True,
                "agent_council_enabled": False,
                "screen_observation_enabled": True,
                "camera_access_enabled": True,
                "desktop_control_enabled": False,
            },
            self.path,
        )
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["provider"], "openai")
        self.assertEqual(saved["openai_model"], "gpt-4.1-mini")
        self.assertEqual(saved["ollama_model"], "qwen2.5:3b")
        self.assertTrue(saved["voice_enabled"])
        self.assertTrue(saved["background_evolution_enabled"])
        self.assertFalse(saved["agent_council_enabled"])
        self.assertTrue(saved["screen_observation_enabled"])
        self.assertTrue(saved["camera_access_enabled"])
        self.assertFalse(saved["desktop_control_enabled"])
        self.assertEqual(raw["provider"], "openai")
        self.assertEqual(raw["custom_setting"], "keep-me")

    def test_invalid_changes_do_not_mutate_file(self):
        before = self.path.read_text(encoding="utf-8")
        invalid_changes = (
            {"ollama_model": "../../unsafe"},
            {"provider": "mock"},
            {"provider": "deepseek", "deepseek_model": "../../unsafe"},
            {"arbitrary_command": "run"},
            {"voice_enabled": "yes"},
        )
        for change in invalid_changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                save_user_settings(change, self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_malformed_config_is_not_overwritten(self):
        self.path.write_text("bad: [yaml", encoding="utf-8")
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            save_user_settings({"voice_enabled": True}, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)


    def test_cloud_provider_models_are_editable_without_saving_credentials(self):
        saved = save_user_settings(
            {"provider": "deepseek", "deepseek_model": "deepseek-chat"},
            self.path,
        )
        self.assertEqual(saved["provider"], "deepseek")
        self.assertEqual(saved["deepseek_model"], "deepseek-chat")
        self.assertNotIn("DEEPSEEK_API_KEY", self.path.read_text(encoding="utf-8"))
        saved = save_user_settings(
            {"provider": "anthropic", "anthropic_model": ""},
            self.path,
        )
        self.assertEqual(saved["provider"], "anthropic")
        self.assertEqual(saved["anthropic_model"], "")
        self.assertEqual(read_user_settings(self.path)["deepseek_model"], "deepseek-chat")


if __name__ == "__main__":
    unittest.main()
