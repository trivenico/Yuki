import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.portable_paths import config_path, data_root, workspace_path


class PortablePathTests(unittest.TestCase):
    def test_default_data_root_is_repository_root(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(data_root(), Path(__file__).resolve().parents[1])

    def test_external_home_receives_relative_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"YUKI_HOME": directory}, clear=True):
                self.assertEqual(data_root(), Path(directory).resolve())
                self.assertEqual(workspace_path("./yuki_workspace"), (Path(directory) / "yuki_workspace").resolve())

    def test_absolute_workspace_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            absolute = Path(directory).resolve()
            with patch.dict(os.environ, {"YUKI_HOME": "/not-used"}, clear=True):
                self.assertEqual(workspace_path(str(absolute)), absolute)

    def test_frozen_build_initializes_writable_per_user_config(self):
        from core import portable_paths

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "config.yaml").write_text("provider: ollama\nollama_model: llama3.2:3b\n", encoding="utf-8")
            user_data = root / "user-data"
            with patch.object(portable_paths.sys, "frozen", True, create=True), patch.object(
                portable_paths.sys, "_MEIPASS", str(bundle), create=True
            ), patch.dict(os.environ, {"XDG_DATA_HOME": str(user_data)}, clear=True):
                config = config_path()
                self.assertEqual(config, (user_data / "Yuki" / "config.yaml").resolve())
                self.assertIn("provider: ollama", config.read_text(encoding="utf-8"))
                self.assertEqual(workspace_path("./yuki_workspace"),
                                 (user_data / "Yuki" / "yuki_workspace").resolve())

    def test_frozen_relative_home_override_is_anchored_to_user_home(self):
        from core import portable_paths

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(portable_paths.sys, "frozen", True, create=True), patch.dict(
                os.environ, {"YUKI_HOME": "YukiData"}, clear=True
            ), patch.object(portable_paths.Path, "home", return_value=Path(temporary)):
                self.assertEqual(data_root(), (Path(temporary) / "YukiData").resolve())

    def test_config_override_can_be_absolute(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "portable-config.yaml"
            config.write_text("name: Yuki\n", encoding="utf-8")
            with patch.dict(os.environ, {"YUKI_CONFIG": str(config)}, clear=True):
                self.assertEqual(config_path(), config.resolve())


if __name__ == "__main__":
    unittest.main()
