import tempfile
import unittest
from pathlib import Path

from core.onboarding import onboarding_needed, save_config, load_user_config, validate_config


class OnboardingConfigurationTests(unittest.TestCase):
    def test_missing_config_triggers_first_run_and_saved_config_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertTrue(onboarding_needed(path))
            save_config({"provider": "mock", "model": "mock"}, path)
            self.assertFalse(onboarding_needed(path))
            self.assertEqual(load_user_config(path)["provider"], "mock")
            if path.stat().st_mode & 0o077:
                self.fail("Configuration file permissions must not grant group/other access.")

    def test_cannot_disable_required_guardrails(self):
        with self.assertRaises(ValueError):
            validate_config({"provider": "mock", "guardrails": {"require_tool_approval": False}})
        with self.assertRaises(ValueError):
            validate_config({"provider": "mock", "guardrails": {"require_cloud_approval": False}})

    def test_rejects_malformed_mcp_endpoints_and_environment_names(self):
        with self.assertRaises(ValueError):
            validate_config({"provider": "mock", "mcp_servers": [
                {"name": "bad", "endpoint": "https://", "allowed_tools": []}]})
        with self.assertRaises(ValueError):
            validate_config({"provider": "openai", "api_key_env": "OPENAI_API_KEY;print(1)"})

    def test_config_does_not_persist_raw_api_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config({"provider": "openai", "model": "example", "api_key_env": "OPENAI_API_KEY",
                         "api_key": "should-never-be-stored"}, path)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("should-never-be-stored", raw)
            self.assertIn("OPENAI_API_KEY", raw)


if __name__ == "__main__":
    unittest.main()
