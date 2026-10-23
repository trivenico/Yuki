import os
import stat
import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService, _prepare_auth_storage


class AuthStoragePermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are not portable to Windows")
    def test_database_file_is_owner_only_and_directory_is_private(self):
        private_dir = self.root / "private"
        private_dir.mkdir(mode=0o700)
        private_dir.chmod(0o700)
        db_path = private_dir / "auth.sqlite3"
        service = BuiltInAuthService(str(db_path), password_iterations=100_000)
        self.assertEqual(service.list_users(), [])
        self.assertEqual(stat.S_IMODE(db_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(private_dir.stat().st_mode) & 0o077, 0)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are not portable to Windows")
    def test_rejects_group_or_world_accessible_directory(self):
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        with self.assertRaisesRegex(PermissionError, "accessible to group/others"):
            BuiltInAuthService(str(shared / "auth.sqlite3"), password_iterations=100_000)

    def test_rejects_database_symlink(self):
        target = self.root / "target.sqlite3"
        target.write_text("not an auth database", encoding="utf-8")
        link = self.root / "link.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this platform")
        with self.assertRaisesRegex(PermissionError, "regular file"):
            _prepare_auth_storage(str(link))

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits are not portable to Windows")
    def test_restricts_permissions_of_existing_database(self):
        private_dir = self.root / "private"
        private_dir.mkdir(mode=0o700)
        private_dir.chmod(0o700)
        db_path = private_dir / "auth.sqlite3"
        db_path.write_text("", encoding="utf-8")
        db_path.chmod(0o644)
        _prepare_auth_storage(str(db_path))
        self.assertEqual(stat.S_IMODE(db_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
