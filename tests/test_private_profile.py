import os
import stat
import tempfile
import unittest
from pathlib import Path

from core.private_profile import (
    PrivateProfileError,
    ensure_private_profile_directory,
)


@unittest.skipUnless(os.name == "posix", "POSIX ownership and mode semantics only")
class PrivateProfileDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def test_creates_owner_only_directory(self):
        target = ensure_private_profile_directory(self.root / "profile")
        self.assertTrue(target.is_dir())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertEqual(target.stat().st_uid, os.geteuid())

    def test_rejects_existing_permissive_directory_without_chmodding_it(self):
        target = self.root / "shared-profile"
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        with self.assertRaises(PrivateProfileError) as caught:
            ensure_private_profile_directory(target)
        self.assertEqual(str(caught.exception), "private_profile_permissions_too_broad")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_rejects_final_component_symlink(self):
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        link = self.root / "profile-link"
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(PrivateProfileError):
            ensure_private_profile_directory(link)

    def test_rejects_file_at_profile_path(self):
        target = self.root / "not-a-directory"
        target.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(PrivateProfileError):
            ensure_private_profile_directory(target)

    def test_rejects_symlink_in_parent_component(self):
        actual = self.root / "actual-parent"
        actual.mkdir(mode=0o700)
        link = self.root / "linked-parent"
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(PrivateProfileError):
            ensure_private_profile_directory(link / "profile")

    def test_rejects_group_or_other_writable_parent(self):
        parent = self.root / "shared-parent"
        parent.mkdir(mode=0o700)
        parent.chmod(0o770)
        with self.assertRaises(PrivateProfileError) as caught:
            ensure_private_profile_directory(parent / "profile")
        self.assertEqual(
            str(caught.exception),
            "private_profile_ancestor_permissions_too_broad",
        )
        self.assertFalse((parent / "profile").exists())

    def test_rejects_empty_path(self):
        with self.assertRaises(PrivateProfileError):
            ensure_private_profile_directory("")


if __name__ == "__main__":
    unittest.main()
