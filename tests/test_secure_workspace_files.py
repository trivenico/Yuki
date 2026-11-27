"""Tests for descriptor-relative, no-follow workspace file operations."""
import os
from pathlib import Path
import tempfile
import unittest

from core.secure_workspace_files import SecureWorkspaceFiles, WorkspaceFileAccessError
from core.workspace_resource_scope import WorkspaceResourceScopeResolver


@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "requires POSIX O_NOFOLLOW")
class SecureWorkspaceFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "workspace"
        self.root.mkdir()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "note.txt").write_bytes(b"original")
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_bytes(b"secret")
        self.resolver = WorkspaceResourceScopeResolver(self.root, workspace_id="owner")
        self.files = SecureWorkspaceFiles(self.resolver, max_read_bytes=1024, max_write_bytes=1024)

    def tearDown(self):
        self.files.close()
        self.tmp.cleanup()

    def test_reads_and_atomically_replaces_regular_file(self):
        resource = self.resolver.resolve("docs/note.txt")
        self.assertEqual(self.files.read_bytes(resource), b"original")
        self.files.write_bytes(resource, b"updated")
        self.assertEqual((self.root / "docs" / "note.txt").read_bytes(), b"updated")
        self.assertEqual(self.files.read_bytes(resource), b"updated")
        self.assertEqual((self.root / "docs" / "note.txt").stat().st_mode & 0o777, 0o600)

    def test_creates_new_file_with_private_mode(self):
        resource = self.resolver.resolve("docs/new.txt")
        self.files.write_bytes(resource, b"new")
        self.assertEqual((self.root / "docs" / "new.txt").read_bytes(), b"new")
        self.assertEqual((self.root / "docs" / "new.txt").stat().st_mode & 0o777, 0o600)

    def test_read_rejects_final_symlink(self):
        link = self.root / "docs" / "link.txt"
        link.symlink_to(self.outside / "secret.txt")
        resource = self.resolver.resolve("docs/link.txt")
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(resource)

    def test_write_replaces_final_symlink_without_touching_target(self):
        link = self.root / "docs" / "link.txt"
        link.symlink_to(self.outside / "secret.txt")
        resource = self.resolver.resolve("docs/link.txt")
        self.files.write_bytes(resource, b"safe replacement")
        self.assertFalse(link.is_symlink())
        self.assertEqual(link.read_bytes(), b"safe replacement")
        self.assertEqual((self.outside / "secret.txt").read_bytes(), b"secret")

    def test_parent_symlink_escape_is_rejected(self):
        link = self.root / "outside-link"
        link.symlink_to(self.outside, target_is_directory=True)
        resource = self.resolver.resolve("outside-link/secret.txt")
        # If changed after resolution, both the revalidation and no-follow
        # descriptor walk must reject the path before opening the outside file.
        link.unlink()
        link.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(resource)
        self.assertEqual((self.outside / "secret.txt").read_bytes(), b"secret")

    def test_resource_from_another_workspace_is_rejected(self):
        other = WorkspaceResourceScopeResolver(self.root, workspace_id="other")
        resource = other.resolve("docs/note.txt")
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(resource)

    def test_read_and_write_limits_are_enforced(self):
        resource = self.resolver.resolve("docs/note.txt")
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.write_bytes(resource, b"x" * 1025)
        (self.root / "docs" / "large.txt").write_bytes(b"x" * 1025)
        large = self.resolver.resolve("docs/large.txt")
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(large)

    def test_closed_handle_rejects_operations(self):
        resource = self.resolver.resolve("docs/note.txt")
        self.files.close()
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(resource)


if __name__ == "__main__":
    unittest.main()
