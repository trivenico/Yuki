"""Tests for canonical workspace resource scope resolution."""
import os
from pathlib import Path
import tempfile
import unittest

from core.workspace_resource_scope import (
    WorkspaceResourceScopeResolver,
    WorkspaceScopeError,
)


class WorkspaceResourceScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "workspace"
        self.root.mkdir()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "readme.md").write_text("safe", encoding="utf-8")
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("secret", encoding="utf-8")
        self.resolver = WorkspaceResourceScopeResolver(self.root, workspace_id="owner-workspace")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_existing_file_and_nonexistent_leaf(self):
        file_resource = self.resolver.resolve("docs/readme.md")
        self.assertEqual(file_resource.relative_path, "docs/readme.md")
        self.assertEqual(Path(file_resource.canonical_path), self.root / "docs" / "readme.md")
        new_resource = self.resolver.resolve("docs/new.txt")
        self.assertEqual(new_resource.relative_path, "docs/new.txt")
        self.assertEqual(Path(new_resource.canonical_path), self.root / "docs" / "new.txt")

    def test_rejects_absolute_traversal_drive_and_control_paths(self):
        invalid = (
            "../outside/secret.txt",
            "docs/../../outside/secret.txt",
            str(self.outside / "secret.txt"),
            "C:\\Windows\\win.ini",
            "\\\\server\\share\\file",
            "docs/secret\x00.txt",
            "",
            "x" * 4097,
        )
        for path in invalid:
            with self.subTest(path=repr(path)), self.assertRaises(WorkspaceScopeError):
                self.resolver.resolve(path)

    def test_symlink_escape_is_rejected(self):
        link = self.root / "escape"
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(WorkspaceScopeError):
            self.resolver.resolve("escape/secret.txt")

    def test_symlink_to_inside_workspace_resolves_to_canonical_target(self):
        link = self.root / "docs-link"
        try:
            link.symlink_to(self.root / "docs", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        resource = self.resolver.resolve("docs-link/readme.md")
        self.assertEqual(resource.relative_path, "docs/readme.md")
        self.assertEqual(Path(resource.canonical_path), self.root / "docs" / "readme.md")

    def test_root_requires_explicit_allow_root(self):
        with self.assertRaises(WorkspaceScopeError):
            self.resolver.resolve(".")
        resource = self.resolver.resolve(".", allow_root=True)
        self.assertEqual(resource.relative_path, ".")

    def test_scope_id_is_stable_and_binds_workspace_identity(self):
        first = self.resolver.resolve("docs/readme.md")
        second = self.resolver.resolve("docs/readme.md")
        other = WorkspaceResourceScopeResolver(self.root, workspace_id="another-workspace")
        third = other.resolve("docs/readme.md")
        self.assertEqual(first.scope_id, second.scope_id)
        self.assertNotEqual(first.scope_id, third.scope_id)
        self.assertEqual(len(first.scope_id), 64)

    def test_workspace_root_must_exist_and_be_directory(self):
        with self.assertRaises((FileNotFoundError, WorkspaceScopeError)):
            WorkspaceResourceScopeResolver(self.root / "missing")
        file_path = self.root / "docs" / "readme.md"
        with self.assertRaises(WorkspaceScopeError):
            WorkspaceResourceScopeResolver(file_path)


if __name__ == "__main__":
    unittest.main()
