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
