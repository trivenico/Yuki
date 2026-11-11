import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.config as config_module
from core.config import load_config
from core.portable_paths import config_path, data_root


class CliPackagingTests(unittest.TestCase):
    def test_installed_layout_uses_user_owned_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "site-packages"
            root.mkdir()
            with patch("core.portable_paths.REPOSITORY_ROOT", root), patch.dict(os.environ, {"APPDATA": str(Path(directory) / "roaming"), "LOCALAPPDATA": str(Path(directory) / "local"), "USERPROFILE": directory, "HOME": directory}, clear=True):
                self.assertNotEqual(config_path(), (root / "config.yaml").resolve())
                self.assertNotEqual(data_root(), root.resolve())
                self.assertTrue(config_path().is_absolute())
                self.assertTrue(data_root().is_absolute())

    def test_default_config_is_created_once_for_installed_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config" / "yuki.yaml"
            with patch.object(config_module, "resolve_config_path", return_value=target), patch.dict(os.environ, {}, clear=True):
                loaded = load_config()
                self.assertEqual(loaded["name"], "Yuki")
                self.assertTrue(target.is_file())
                original = target.read_text(encoding="utf-8")
                target.write_text(original + "\n# owner customization\n", encoding="utf-8")
                load_config()
                self.assertIn("# owner customization", target.read_text(encoding="utf-8"))

    def test_explicit_config_path_is_not_silently_created(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.yaml"
            with self.assertRaises(RuntimeError):
                load_config(path=missing)
            self.assertFalse(missing.exists())

    def test_project_metadata_exposes_stable_console_entrypoint(self):
        metadata = Path(__file__).resolve().parents[1] / "pyproject.toml"
        content = metadata.read_text(encoding="utf-8")
        self.assertIn('name = "yuki-ai-assistant"', content)
        self.assertIn('yuki = "yuki_cli:main"', content)
        self.assertIn('"yuki_cli"', content)
        self.assertIn('core = ["default_config.yaml"]', content)


if __name__ == "__main__":
    unittest.main()
