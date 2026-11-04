"""Windows backup script integration tests (run on Windows CI)."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "daily_backup.bat"


@unittest.skipUnless(os.name == "nt", "Windows backup integration tests")
class WindowsBackupScriptTests(unittest.TestCase):
    def run_backup(self, source, destination):
        return subprocess.run(
            ["cmd.exe", "/c", str(SCRIPT), str(source), str(destination)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=90,
        )

    def test_backup_preserves_hidden_files_and_archive_is_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "YukiData"
            destination = root / "Backups"
            source.mkdir()
            (source / "visible.json").write_text('{"ok":true}', encoding="utf-8")
            (source / ".private-state").write_text("hidden", encoding="utf-8")
            result = self.run_backup(source, destination)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            archives = list(destination.glob("yuki-data-*.zip"))
            self.assertEqual(len(archives), 1)
            with zipfile.ZipFile(archives[0]) as archive:
                self.assertEqual(archive.testzip(), None)
                self.assertEqual(archive.read("visible.json"), b'{"ok":true}')
                self.assertEqual(archive.read(".private-state"), b"hidden")

    def test_backup_rejects_same_or_nested_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "YukiData"
            source.mkdir()
            (source / "note.txt").write_text("private", encoding="utf-8")
            same = self.run_backup(source, source)
            self.assertNotEqual(same.returncode, 0)
            nested = self.run_backup(source, source / "nested-backup")
            self.assertNotEqual(nested.returncode, 0)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.name == "posix", "POSIX backup integration tests")
class PosixBackupScriptTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "daily_backup.sh"

    def run_backup(self, env, *args):
        merged = os.environ.copy()
        merged.update(env)
        return subprocess.run(
            ["sh", str(self.SCRIPT), *map(str, args)],
            cwd=str(ROOT), env=merged, capture_output=True, text=True, timeout=90,
        )

    def test_default_source_tracks_configured_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "custom-yuki-home" / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "private.json").write_text('{"safe":true}', encoding="utf-8")
            destination = root / "backup"
            result = self.run_backup({
                "YUKI_HOME": str(root / "custom-yuki-home"),
                "YUKI_WORKSPACE": "workspace",
                "YUKI_BACKUP_DIR": str(destination),
                "YUKI_BACKUP_RETENTION_DAYS": "14",
            })
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            archives = list(destination.glob("yuki-data-*.tar.gz"))
            self.assertEqual(len(archives), 1)
            listing = subprocess.run(
                ["tar", "-tzf", str(archives[0])],
                capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            self.assertIn("workspace/private.json", listing)

    def test_explicit_backup_source_overrides_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            explicit = root / "explicit-source"
            workspace.mkdir()
            explicit.mkdir()
            (workspace / "wrong.txt").write_text("wrong", encoding="utf-8")
            (explicit / "right.txt").write_text("right", encoding="utf-8")
            destination = root / "backup"
            result = self.run_backup({
                "YUKI_WORKSPACE": str(workspace),
                "YUKI_BACKUP_SOURCE": str(explicit),
                "YUKI_BACKUP_DIR": str(destination),
            })
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            archive = next(destination.glob("yuki-data-*.tar.gz"))
            listing = subprocess.run(
                ["tar", "-tzf", str(archive)],
                capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            self.assertIn("explicit-source/right.txt", listing)
            self.assertNotIn("workspace/wrong.txt", listing)
