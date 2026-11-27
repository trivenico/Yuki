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

    def test_final_symlink_inserted_after_resolution_is_not_followed(self):
        resource = self.resolver.resolve("docs/note.txt")
        original_resolve = self.resolver.resolve

        def race_resolve(path, **kwargs):
            resolved = original_resolve(path, **kwargs)
            if path == resource.relative_path:
                target = self.root / "docs" / "note.txt"
                target.unlink()
                target.symlink_to(self.outside / "secret.txt")
            return resolved

        self.resolver.resolve = race_resolve
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(resource)
        self.assertEqual((self.outside / "secret.txt").read_bytes(), b"secret")

    def test_write_does_not_follow_final_symlink_inserted_after_resolution(self):
        resource = self.resolver.resolve("docs/note.txt")
        original_resolve = self.resolver.resolve

        def race_resolve(path, **kwargs):
            resolved = original_resolve(path, **kwargs)
            if path == resource.relative_path:
                target = self.root / "docs" / "note.txt"
                target.unlink()
                target.symlink_to(self.outside / "secret.txt")
            return resolved

        self.resolver.resolve = race_resolve
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.write_bytes(resource, b"attacker-controlled")
        self.assertEqual((self.outside / "secret.txt").read_bytes(), b"secret")

    def test_parent_symlink_inserted_after_resolution_is_rejected(self):
        resource = self.resolver.resolve("docs/note.txt")
        original_resolve = self.resolver.resolve

        def race_resolve(path, **kwargs):
            resolved = original_resolve(path, **kwargs)
            if path == resource.relative_path:
                docs = self.root / "docs"
                docs.rename(self.root / "docs-original")
                docs.symlink_to(self.outside, target_is_directory=True)
            return resolved

        self.resolver.resolve = race_resolve
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(resource)
        self.assertEqual((self.outside / "secret.txt").read_bytes(), b"secret")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_fifo_is_rejected_without_blocking(self):
        fifo = self.root / "docs" / "pipe"
        os.mkfifo(fifo)
        resource = self.resolver.resolve("docs/pipe")
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(resource)
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.write_bytes(resource, b"not a FIFO write")

    def test_forged_resource_scope_is_rejected(self):
        from dataclasses import replace
        resource = self.resolver.resolve("docs/note.txt")
        forged = replace(resource, scope_id="0" * 64)
        with self.assertRaises(WorkspaceFileAccessError):
            self.files.read_bytes(forged)

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
