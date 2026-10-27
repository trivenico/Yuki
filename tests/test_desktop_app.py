import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.desktop_app import ROOT, build_runtime_command


class DesktopAppTests(unittest.TestCase):
    def test_desktop_uses_the_current_python_and_authoritative_runtime(self):
        command = build_runtime_command()
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "-u")
        self.assertEqual(Path(command[2]), ROOT / "main.py")
        self.assertNotIn("shell", command)

    def test_packaged_desktop_uses_adjacent_runtime_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            desktop = Path(temporary) / "YukiDesktop.exe"
            runtime_dir = Path(temporary) / "YukiRuntime"
            runtime_dir.mkdir()
            runtime = runtime_dir / "YukiRuntime.exe"
            desktop.touch()
            runtime.touch()
            with patch.object(sys, "frozen", True, create=True), patch.object(
                sys, "executable", str(desktop)
            ):
                self.assertEqual(build_runtime_command(), [str(runtime)])

    def test_packaged_desktop_reports_missing_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            desktop = Path(temporary) / "YukiDesktop.exe"
            desktop.touch()
            with patch.object(sys, "frozen", True, create=True), patch.object(
                sys, "executable", str(desktop)
            ):
                with self.assertRaisesRegex(RuntimeError, "not found in the adjacent"):
                    build_runtime_command()

    def test_desktop_root_is_the_repository_root(self):
        self.assertTrue((ROOT / "main.py").is_file())
        self.assertTrue((ROOT / "yuki_cli.py").is_file())


if __name__ == "__main__":
    unittest.main()
