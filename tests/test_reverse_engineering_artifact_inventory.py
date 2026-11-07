import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.artifact_inventory import (
    ArtifactInventoryError,
    inventory_artifact_root,
)


class ArtifactInventoryTests(unittest.TestCase):
    def test_inventory_hashes_files_and_identifies_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"scripts":{"build":"vite"}}', encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text("export const answer = 42;", encoding="utf-8")
            result = inventory_artifact_root(root)
            self.assertEqual(result["file_count"], 2)
            self.assertEqual(result["manifest_paths"], ["package.json"])
            self.assertEqual(
                result["files"][1]["sha256"],
                __import__("hashlib").sha256(b"export const answer = 42;").hexdigest(),
            )

    def test_default_exclusions_are_not_traversed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ignored = root / "node_modules"
            ignored.mkdir()
            (ignored / "large.js").write_text("ignored", encoding="utf-8")
            (root / "main.py").write_text("print('ok')", encoding="utf-8")
            result = inventory_artifact_root(root)
            self.assertEqual([item["path"] for item in result["files"]], ["main.py"])
            self.assertEqual(result["coverage"]["status"], "partial")

    def test_symlinks_are_skipped_without_following(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root, external = Path(temp), Path(outside)
            secret = external / "secret.txt"
            secret.write_text("not part of target", encoding="utf-8")
            link = root / "external-link"
            try:
                link.symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            result = inventory_artifact_root(root)
            self.assertEqual(result["file_count"], 0)
            self.assertEqual(result["skipped_symlinks"], 1)

    def test_file_count_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            with self.assertRaisesRegex(ArtifactInventoryError, "max_files"):
                inventory_artifact_root(root, max_files=1)

    def test_file_and_total_size_limits_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.txt").write_text("12345", encoding="utf-8")
            with self.assertRaisesRegex(ArtifactInventoryError, "max_file_bytes"):
                inventory_artifact_root(root, max_file_bytes=4)
            with self.assertRaisesRegex(ArtifactInventoryError, "max_total_bytes"):
                inventory_artifact_root(root, max_total_bytes=4)

    def test_invalid_roots_and_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ArtifactInventoryError):
                inventory_artifact_root(Path(temp) / "missing")
            with self.assertRaises(ArtifactInventoryError):
                inventory_artifact_root(temp, max_files=0)

    def test_directory_symlink_root_resolves_to_explicit_target(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp) / "root-link"
            try:
                root.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            (Path(outside) / "a.txt").write_text("x", encoding="utf-8")
            result = inventory_artifact_root(root)
            self.assertEqual(result["file_count"], 1)


if __name__ == "__main__":
    unittest.main()
