import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

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

    def test_snapshot_rejects_nested_backup_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "separate directories"):
                create_snapshot(source, source / "backup", root)


if __name__ == "__main__":
    unittest.main()
