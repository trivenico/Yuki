import tempfile
import unittest
from pathlib import Path

from tools.desktop_smoke_test import executable_for


class DesktopSmokeTestTests(unittest.TestCase):
    def test_finds_windows_linux_or_macos_bundle_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if __import__("sys").platform == "darwin":
                binary = root / "Contents" / "MacOS" / "Yuki"
            elif __import__("sys").platform == "win32":
                binary = root / "Yuki.exe"
            else:
                binary = root / "Yuki"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"test")
            self.assertEqual(executable_for(root), binary)

    def test_resolves_macos_app_bundle_from_pyinstaller_base_name(self):
        if __import__("sys").platform != "darwin":
            self.skipTest("macOS app bundle path is platform-specific")
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Yuki.app"
            binary = app / "Contents" / "MacOS" / "Yuki"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"test")
            self.assertEqual(executable_for(Path(directory) / "Yuki"), binary)

    def test_resolves_macos_app_bundle_when_onedir_directory_also_exists(self):
        if __import__("sys").platform != "darwin":
            self.skipTest("macOS app bundle path is platform-specific")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "Yuki"
            base.mkdir()
            binary = root / "Yuki.app" / "Contents" / "MacOS" / "Yuki"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"test")
            self.assertEqual(executable_for(base), binary)

    def test_missing_executable_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                executable_for(Path(directory))


if __name__ == "__main__":
    unittest.main()
