import hashlib
import tempfile
import unittest
from pathlib import Path

from core.task_verifier import verify_workspace_file


class WorkspaceFileVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()

    def test_reports_sha256_size_and_limited_scope(self):
        artifact = self.root / "report.txt"
        artifact.write_text("Yuki artifact", encoding="utf-8")
        result = verify_workspace_file(self.root, "report.txt")
        self.assertEqual(result["path"], "report.txt")
        self.assertEqual(result["size_bytes"], len(b"Yuki artifact"))
        self.assertEqual(result["sha256"], hashlib.sha256(b"Yuki artifact").hexdigest())
        self.assertEqual(result["scope"], "file_presence_and_identity_only")

    def test_literal_text_acceptance_check_reports_match_and_mismatch(self):
        artifact = self.root / "report.txt"
        artifact.write_text("Project status: ready", encoding="utf-8")
        matched = verify_workspace_file(self.root, "report.txt", expected_text="status: ready")
        missed = verify_workspace_file(self.root, "report.txt", expected_text="deployment succeeded")
        self.assertTrue(matched["content_match"])
        self.assertFalse(missed["content_match"])
        self.assertEqual(matched["scope"], "file_identity_and_literal_text_match")

    def test_literal_text_check_rejects_empty_or_oversized_expectations(self):
        artifact = self.root / "report.txt"
        artifact.write_text("report", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            verify_workspace_file(self.root, "report.txt", expected_text="")
        with self.assertRaisesRegex(ValueError, "4000"):
            verify_workspace_file(self.root, "report.txt", expected_text="x" * 4001)

    def test_literal_text_check_rejects_binary_and_large_files(self):
        binary = self.root / "binary.bin"
        binary.write_bytes(b"\\xff\\xfe")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            verify_workspace_file(self.root, "binary.bin", expected_text="hello")
        large = self.root / "large.txt"
        with large.open("wb") as handle:
            handle.truncate(1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            verify_workspace_file(self.root, "large.txt", expected_text="hello")

    def test_rejects_absolute_and_traversal_paths(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Absolute paths"):
            verify_workspace_file(self.root, str(outside))
        with self.assertRaisesRegex(ValueError, "inside"):
            verify_workspace_file(self.root, "../outside.txt")

    def test_rejects_missing_directory_and_non_file_targets(self):
        with self.assertRaisesRegex(ValueError, "inside"):
            verify_workspace_file(self.root, "missing.txt")
        with self.assertRaisesRegex(ValueError, "regular file"):
            verify_workspace_file(self.root, ".")

    def test_rejects_symlink_artifacts(self):
        target = self.root / "target.txt"
        target.write_text("target", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is not permitted in this environment.")
        with self.assertRaisesRegex(ValueError, "Symbolic links"):
            verify_workspace_file(self.root, "link.txt")


if __name__ == "__main__":
    unittest.main()
