import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config


class LocalFirstConfigDefaultsTests(unittest.TestCase):
    def test_missing_provider_setting_defaults_to_ollama_not_mock_or_cloud(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("workspace: ./workspace\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(config_path)
        self.assertEqual(config["provider"], "ollama")


if __name__ == "__main__":
    unittest.main()
