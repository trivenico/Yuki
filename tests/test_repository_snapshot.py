import tempfile
import unittest
from pathlib import Path

from automation.repository_snapshot import (
    RepositorySnapshotBuilder, RepositorySnapshotError,
)


class RepositorySnapshotTests(unittest.TestCase):
    def test_snapshot_is_deterministic_independent_of_mapping_order(self):
        builder = RepositorySnapshotBuilder()
        first = builder.build({"b.py": b"b", "a.py": b"a"})
        second = builder.build({"a.py": b"a", "b.py": b"b"})
        self.assertEqual(first, second)

    def test_materializes_exact_bytes_and_checks_expected_digest(self):
        builder = RepositorySnapshotBuilder()
        files = {"src/main.py": b"print('yuki')\n", "README.md": b"# Yuki\n"}
        snapshot = builder.build(files)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "workspace"
            materialized = builder.materialize(
                files, destination,
                expected_snapshot_sha256=snapshot.snapshot_sha256,
            )
            self.assertEqual(materialized, snapshot)
            self.assertEqual((destination / "src/main.py").read_bytes(), files["src/main.py"])
            self.assertEqual((destination / "README.md").read_bytes(), files["README.md"])

    def test_rejects_path_traversal_and_absolute_paths(self):
        builder = RepositorySnapshotBuilder()
        for path in ("../escape", "/absolute", "a/../escape", "a\\escape"):
            with self.subTest(path=path), self.assertRaises(RepositorySnapshotError):
                builder.build({path: b"x"})

    def test_rejects_oversized_files_and_snapshots(self):
        with self.assertRaisesRegex(RepositorySnapshotError, "file_size"):
            RepositorySnapshotBuilder(max_file_bytes=2).build({"a": b"123"})
        with self.assertRaisesRegex(RepositorySnapshotError, "total_size"):
            RepositorySnapshotBuilder(max_total_bytes=2).build({"a": b"12", "b": b"3"})

    def test_excludes_generated_directories(self):
        snapshot = RepositorySnapshotBuilder().build({
            "src/main.py": b"ok",
            ".git/config": b"secret",
            "node_modules/pkg/index.js": b"generated",
        })
        self.assertEqual([item.path for item in snapshot.files], ["src/main.py"])

    def test_refuses_nonempty_destination_and_digest_mismatch(self):
        builder = RepositorySnapshotBuilder()
        files = {"a.txt": b"a"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "occupied"
            root.mkdir()
            (root / "keep.txt").write_text("keep")
            with self.assertRaisesRegex(RepositorySnapshotError, "new_or_empty"):
                builder.materialize(files, root)
            with self.assertRaisesRegex(RepositorySnapshotError, "digest_mismatch"):
                builder.materialize(files, Path(temp) / "fresh", expected_snapshot_sha256="0" * 64)


if __name__ == "__main__":
    unittest.main()
