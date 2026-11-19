import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from automation.sandbox_artifacts import (
    ArtifactValidationError,
    validate_artifact_directory,
)


class SandboxArtifactValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_manifest_is_deterministic_and_contains_file_digests(self):
        (self.root / "b.txt").write_bytes(b"second")
        (self.root / "a.json").write_bytes(b'{"ok":true}')
        first = validate_artifact_directory(
            self.root, allowed_extensions=(".txt", ".json")
        )
        second = validate_artifact_directory(
            self.root, allowed_extensions=(".json", ".txt")
        )
        self.assertEqual(first, second)
        self.assertEqual([item.path for item in first.files], ["a.json", "b.txt"])
        self.assertEqual(first.files[0].sha256, hashlib.sha256(b'{"ok":true}').hexdigest())
        self.assertEqual(first.total_bytes, len(b'{"ok":true}') + len(b"second"))
        self.assertEqual(len(first.digest), 64)

    def test_rejects_unapproved_extension_and_secret_names(self):
        (self.root / "payload.exe").write_bytes(b"binary")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        (self.root / "payload.exe").unlink()
        (self.root / ".env").write_text("SECRET=x", encoding="utf-8")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".env", ".txt"))

    def test_rejects_symlink_and_hard_link(self):
        target = self.root / "target.txt"
        target.write_text("data", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        link.unlink()
        hardlink = self.root / "hardlink.txt"
        try:
            os.link(target, hardlink)
        except OSError:
            self.skipTest("hard links unavailable")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))

    def test_rejects_file_count_and_size_limits(self):
        (self.root / "a.txt").write_bytes(b"12345")
        (self.root / "b.txt").write_bytes(b"67890")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_files=1
            )
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_file_bytes=4
            )
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_total_bytes=9
            )

    def test_rejects_empty_directory_and_symlink_root(self):
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        alias = self.root.parent / (self.root.name + "-alias")
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        self.addCleanup(lambda: alias.unlink(missing_ok=True))
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(alias, allowed_extensions=(".txt",))

    def test_rejects_special_files_and_invalid_limits(self):
        fifo = self.root / "pipe.txt"
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unsupported")
        os.mkfifo(fifo)
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        fifo.unlink()
        (self.root / "ok.txt").write_text("ok", encoding="utf-8")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_files=True
            )


if __name__ == "__main__":
    unittest.main()
