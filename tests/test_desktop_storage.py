import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.desktop.storage import DesktopStorageError, prepare_private_desktop_root


class DesktopStorageTests(unittest.TestCase):
    def test_creates_private_posix_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = prepare_private_desktop_root(Path(directory) / "private")
            self.assertTrue(root.is_dir())
            if os.name == "posix":
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertEqual(root.stat().st_uid, os.geteuid())

    def test_rejects_symlink_root(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir()
            link = base / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            with self.assertRaisesRegex(DesktopStorageError, "symbolic link"):
                prepare_private_desktop_root(link)

    def test_rejects_non_directory_root(self):
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "not-a-directory"
            file_path.write_text("data", encoding="utf-8")
            with self.assertRaises(DesktopStorageError):
                prepare_private_desktop_root(file_path)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions are not used on Windows")
    def test_fails_closed_when_private_permissions_cannot_be_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "private"
            with patch.object(Path, "chmod", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(DesktopStorageError, "private permissions"):
                    prepare_private_desktop_root(root)
            self.assertTrue(root.is_dir())


if __name__ == "__main__":
    unittest.main()
