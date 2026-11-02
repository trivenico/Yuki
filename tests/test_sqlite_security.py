import os
import tempfile
import unittest
from pathlib import Path

from core.sqlite_security import prepare_sqlite_path, secure_sqlite_file


class SQLiteSecurityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "requires O_NOFOLLOW")
    def test_prepare_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.db"
            target.write_bytes(b"fixture")
            target.chmod(0o644)
            link = root / "linked.db"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                prepare_sqlite_path(str(link))
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_prepare_creates_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "approval.db"
            prepare_sqlite_path(str(path))
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_sidecar_permissions_are_restricted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.db"
            sidecar = Path(str(path) + "-journal")
            path.write_bytes(b"db")
            sidecar.write_bytes(b"journal")
            path.chmod(0o644)
            sidecar.chmod(0o644)
            secure_sqlite_file(str(path))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
