import hashlib
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path

from core.immutable_workspace_staging import (
    WorkspaceStagingError,
    stage_workspace_snapshot,
)


@unittest.skipUnless(os.name == "posix", "POSIX descriptor and permission semantics only")
class ImmutableWorkspaceStagingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.source = self.base / "workspace"
        self.staging = self.base / "staging"
        self.source.mkdir(mode=0o700)
        self.staging.mkdir(mode=0o700)
        self.source.chmod(0o700)
        self.staging.chmod(0o700)

    def stage(self, paths, **kwargs):
        return stage_workspace_snapshot(
            self.source, self.staging,
            attempt_id="attempt_0123456789abcdef",
            relative_paths=paths, **kwargs,
        )

    def test_stages_only_explicit_regular_files_and_returns_manifest(self):
        (self.source / "src").mkdir(mode=0o700)
        (self.source / "src" / "main.py").write_text("print('safe')\n", encoding="utf-8")
        (self.source / ".env").write_text("SECRET=not-staged", encoding="utf-8")

        snapshot = self.stage(["src/main.py"])
        staged = Path(snapshot.path)
        self.assertTrue(staged.is_dir())
        self.assertEqual((staged / "src" / "main.py").read_text(encoding="utf-8"), "print('safe')\n")
        self.assertFalse((staged / ".env").exists())
        self.assertEqual(stat.S_IMODE((staged / "src").stat().st_mode), 0o500)
        self.assertEqual(stat.S_IMODE((staged / "src" / "main.py").stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o500)
        self.assertEqual(stat.S_IMODE((staged / "manifest.json").stat().st_mode), 0o400)
        manifest = json.loads((staged / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["attempt_id"], snapshot.attempt_id)
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual(snapshot.manifest_sha256, hashlib.sha256(snapshot.manifest_json.encode()).hexdigest())
        self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(b"print('safe')\n").hexdigest())

    def test_same_attempt_gets_distinct_snapshot_ids_without_overwrite(self):
        (self.source / "a.txt").write_text("one", encoding="utf-8")
        first = self.stage(["a.txt"])
        (self.source / "a.txt").write_text("two", encoding="utf-8")
        second = self.stage(["a.txt"])
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual((Path(first.path) / "a.txt").read_text(), "one")
        self.assertEqual((Path(second.path) / "a.txt").read_text(), "two")

    def test_rejects_symlink_file(self):
        outside = self.base / "outside.txt"
        outside.write_text("not allowed", encoding="utf-8")
        (self.source / "link.txt").symlink_to(outside)
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["link.txt"])

    def test_rejects_symlink_parent(self):
        outside = self.base / "outside"
        outside.mkdir(mode=0o700)
        (outside / "file.txt").write_text("not allowed", encoding="utf-8")
        (self.source / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["linked/file.txt"])

    def test_rejects_hardlink(self):
        original = self.source / "original.txt"
        original.write_text("shared inode", encoding="utf-8")
        os.link(original, self.source / "alias.txt")
        with self.assertRaises(WorkspaceStagingError) as caught:
            self.stage(["original.txt"])
        self.assertEqual(str(caught.exception), "workspace_hardlink_not_allowed")

    def test_rejects_fifo(self):
        os.mkfifo(self.source / "pipe")
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["pipe"])

    def test_rejects_unix_socket(self):
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        sock.bind(str(self.source / "socket"))
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["socket"])

    def test_rejects_traversal_and_noncanonical_paths(self):
        for path in ("../outside", "a/../b", "/absolute", "a//b", "a/./b", "a\\b"):
            with self.subTest(path=path), self.assertRaises(WorkspaceStagingError):
                self.stage([path])

    def test_rejects_casefold_collision_and_file_directory_collision(self):
        (self.source / "A.txt").write_text("A", encoding="utf-8")
        (self.source / "a.txt").write_text("a", encoding="utf-8")
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["A.txt", "a.txt"])
        (self.source / "node").write_text("file", encoding="utf-8")
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["node", "node/child.txt"])

    def test_rejects_casefolded_directory_aliases(self):
        (self.source / "DirA").mkdir(mode=0o700)
        (self.source / "dira").mkdir(mode=0o700)
        (self.source / "DirA" / "one.txt").write_text("one", encoding="utf-8")
        (self.source / "dira" / "two.txt").write_text("two", encoding="utf-8")
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["DirA/one.txt", "dira/two.txt"])

    def test_rejects_parent_traversal_in_source_root(self):
        with self.assertRaises(WorkspaceStagingError):
            stage_workspace_snapshot(self.base / "workspace" / ".." / "workspace",
                                     self.staging, attempt_id="attempt_0123456789abcdef",
                                     relative_paths=["x.txt"])

    def test_enforces_per_file_and_total_byte_limits(self):
        (self.source / "large.txt").write_text("123456", encoding="utf-8")
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["large.txt"], max_file_bytes=5)
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["large.txt"], max_total_bytes=5)

    def test_rejects_invalid_attempt_id_and_empty_allowlist(self):
        with self.assertRaises(WorkspaceStagingError):
            stage_workspace_snapshot(self.source, self.staging, attempt_id="../bad",
                                     relative_paths=["anything"])
        with self.assertRaises(WorkspaceStagingError):
            self.stage([])

    def test_failed_staging_does_not_publish_partial_snapshot(self):
        (self.source / "large.txt").write_text("123456", encoding="utf-8")
        with self.assertRaises(WorkspaceStagingError):
            self.stage(["large.txt"], max_file_bytes=2)
        self.assertEqual(list(self.staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
