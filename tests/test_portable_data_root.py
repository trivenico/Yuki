import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.portable_paths import data_root


class PortableDataRootTests(unittest.TestCase):
    def test_yuki_home_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"YUKI_HOME": str(Path(tmp) / "custom")}, clear=True):
                self.assertEqual(data_root(), (Path(tmp) / "custom").resolve())

    def test_windows_defaults_to_local_app_data(self):
        with patch("core.portable_paths.sys.platform", "win32"):
            with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}, clear=True):
                self.assertEqual(data_root(), Path(r"C:\Users\Test\AppData\Local\Yuki"))

    def test_linux_defaults_to_xdg_data_home(self):
        with patch("core.portable_paths.sys.platform", "linux"):
            with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/yuki-xdg"}, clear=True):
                self.assertEqual(data_root(), Path("/tmp/yuki-xdg/Yuki"))

    def test_macos_defaults_to_application_support(self):
        with patch("core.portable_paths.sys.platform", "darwin"):
            with patch.dict(os.environ, {}, clear=True):
                with patch("core.portable_paths.Path.home", return_value=Path("/Users/test")):
                    self.assertEqual(data_root(), Path("/Users/test/Library/Application Support/Yuki"))


if __name__ == "__main__":
    unittest.main()
