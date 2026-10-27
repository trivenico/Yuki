import tempfile
import unittest
from pathlib import Path
import yaml
from core.settings_store import read_user_settings, save_user_settings

class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.yaml"
        self.path.write_text("provider: ollama\ncustom_setting: keep-me\n", encoding="utf-8")
    def tearDown(self):
        self.temp.cleanup()
    def test_safe_defaults(self):
        settings = read_user_settings(self.path)
        self.assertEqual(settings["provider"], "ollama")\n        self.assertEqual(settings["ollama_model"], "llama3.2:3b")
        self.assertFalse(settings["voice_enabled"])
        self.assertTrue(settings["firewall_dry_run"])
    def test_allowlisted_update_preserves_unrelated_settings(self):
        saved = save_user_settings({"provider": "openai", "ollama_model": "qwen2.5:3b", "openai_model": "gpt-4.1-mini", "voice_enabled": True}, self.path)
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["provider"], "openai")\n        self.assertEqual(saved["openai_model"], "gpt-4.1-mini")\n        self.assertEqual(saved["ollama_model"], "qwen2.5:3b")
        self.assertTrue(saved["voice_enabled"])
        self.assertEqual(raw["provider"], "ollama")
        self.assertEqual(raw["custom_setting"], "keep-me")
    def test_invalid_changes_do_not_mutate_file(self):
        before = self.path.read_text(encoding="utf-8")
        for change in ({"ollama_model": "../../unsafe"}, {"provider": "mock"}, {"arbitrary_command": "run"}, {"voice_enabled": "yes"}):
            with self.assertRaises(ValueError):
                save_user_settings(change, self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"), before)
    def test_malformed_config_is_not_overwritten(self):
        self.path.write_text("bad: [yaml", encoding="utf-8")
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            save_user_settings({"voice_enabled": True}, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

if __name__ == "__main__":
    unittest.main()
