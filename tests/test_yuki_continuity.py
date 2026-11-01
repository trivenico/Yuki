import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch

from scripts.yuki_continuity import (
    MANIFEST_NAME, create_snapshot, restore_snapshot, verify_snapshot,
)


class YukiContinuityTests(unittest.TestCase):
    def test_snapshot_excludes_credentials_and_restores_verified_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "YukiData"
            source.mkdir()
            (source / "memory.json").write_text('{"project":"Yuki"}', encoding="utf-8")
            (source / ".env").write_text("API_KEY=do-not-back-up", encoding="utf-8")
            (source / "credentials.json").write_text('{"token":"private"}', encoding="utf-8")
            archive = create_snapshot(source, root / "backups", root)
            manifest = verify_snapshot(archive)
            self.assertEqual(manifest["file_count"], 1)
            restored = root / "restored"
            restore_snapshot(archive, restored)
            self.assertEqual((restored / "memory.json").read_text(encoding="utf-8"), '{"project":"Yuki"}')
            self.assertFalse((restored / ".env").exists())
            self.assertFalse((restored / "credentials.json").exists())

    def test_verification_detects_modified_archive_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data"
            source.mkdir()
            (source / "state.json").write_text("original", encoding="utf-8")
            archive = create_snapshot(source, root / "backups", root)
            tampered = root / "tampered.zip"
            with ZipFile(archive, "r") as original, ZipFile(tampered, "w") as changed:
                for name in original.namelist():
                    data = original.read(name)
                    if name == "data/state.json":
                        data = b"modified"
                    changed.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                verify_snapshot(tampered)

    def test_restore_refuses_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data"
            source.mkdir()
            (source / "state.json").write_text("safe", encoding="utf-8")
            archive = create_snapshot(source, root / "backups", root)
            destination = root / "existing"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty directory"):
                restore_snapshot(archive, destination)
            self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "keep")


    def test_restore_is_atomic_when_final_promotion_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data"
            source.mkdir()
            (source / "state.json").write_text("complete", encoding="utf-8")
            archive = create_snapshot(source, root / "backups", root)
            destination = root / "restored"
            with patch("scripts.yuki_continuity.os.replace", side_effect=OSError("promotion failed")):
                with self.assertRaisesRegex(OSError, "promotion failed"):
                    restore_snapshot(archive, destination)
            self.assertFalse(destination.exists())
            self.assertFalse(list(root.glob(".yuki-restore-*")))

    def test_verification_rejects_traversal_manifest_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "unsafe.zip"
            manifest = {
                "schema_version": 1,
                "file_count": 1,
                "files": {"../escape.txt": "0" * 64},
            }
            with ZipFile(archive, "w") as output:
                output.writestr(MANIFEST_NAME, json.dumps(manifest))
                output.writestr("data/../escape.txt", "unsafe")
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                verify_snapshot(archive)

    def test_restore_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data"
            source.mkdir()
            (source / "state.json").write_text("safe", encoding="utf-8")
            archive = create_snapshot(source, root / "backups", root)
            actual = root / "actual"
            actual.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(actual, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not permitted on this platform")
            with self.assertRaisesRegex(ValueError, "cannot be a symlink"):
                restore_snapshot(archive, link)
            self.assertEqual(list(actual.iterdir()), [])

    def test_git_metadata_never_records_authenticated_remote_url(self):
        from scripts.yuki_continuity import git_metadata
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("scripts.yuki_continuity.subprocess.run") as run:
                def fake_run(command, **kwargs):
                    from subprocess import CompletedProcess
                    if command[-1] == "HEAD":
                        return CompletedProcess(command, 0, "abc123\n", "")
                    if command[-1] == "--show-current":
                        return CompletedProcess(command, 0, "main\n", "")
                    if command[-1] == "origin":
                        return CompletedProcess(command, 0, "https://user:token@example.com/org/repo.git\n", "")
                    raise AssertionError(command)
                run.side_effect = fake_run
                metadata = git_metadata(root)
        self.assertNotIn("remote", metadata)
        self.assertEqual(metadata["commit"], "abc123")
        self.assertEqual(metadata["branch"], "main")

    def test_snapshot_rejects_nested_backup_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "separate directories"):
                create_snapshot(source, source / "backup", root)


if __name__ == "__main__":
    unittest.main()
