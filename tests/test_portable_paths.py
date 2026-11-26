import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.portable_paths import (
    config_path,
    data_root,
    desktop_data_root,
    private_profile_root,
    workspace_path,
)


class PortablePathTests(unittest.TestCase):
    def test_default_data_root_is_repository_root(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(data_root(), Path(__file__).resolve().parents[1])

    def test_external_home_receives_relative_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"YUKI_HOME": directory}, clear=True):
                self.assertEqual(data_root(), Path(directory).resolve())
                self.assertEqual(desktop_data_root(), Path(directory) / ".yuki")
                self.assertEqual(private_profile_root(), Path(directory) / ".yuki" / "private-profile")
                self.assertEqual(workspace_path("./yuki_workspace"), (Path(directory) / "yuki_workspace").resolve())

    def test_source_checkout_private_profile_is_separate_from_workspace(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                private_profile_root(),
                Path(__file__).resolve().parents[1] / ".yuki" / "private-profile",
            )

    def test_private_profile_path_does_not_resolve_yuki_home_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual"
            actual.mkdir()
            link = root / "linked-home"
            link.symlink_to(actual, target_is_directory=True)
            with patch.dict(os.environ, {"YUKI_HOME": str(link)}, clear=True):
                self.assertEqual(
                    private_profile_root(),
                    link / ".yuki" / "private-profile",
                )

    def test_absolute_workspace_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            absolute = Path(directory).resolve()
            with patch.dict(os.environ, {"YUKI_HOME": "/not-used"}, clear=True):
                self.assertEqual(workspace_path(str(absolute)), absolute)

    def test_config_override_can_be_absolute(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "portable-config.yaml"
            config.write_text("name: Yuki\n", encoding="utf-8")
            with patch.dict(os.environ, {"YUKI_CONFIG": str(config)}, clear=True):
                self.assertEqual(config_path(), config.resolve())


if __name__ == "__main__":
    unittest.main()
