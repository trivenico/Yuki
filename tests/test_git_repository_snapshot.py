import subprocess
import tempfile
import unittest
from pathlib import Path

from automation.git_repository_snapshot import GitRepositorySnapshotReader, GitSnapshotError
from automation.repository_snapshot import RepositorySnapshotBuilder


class GitRepositorySnapshotTests(unittest.TestCase):
    def _repo(self, root):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Yuki Tests"], check=True)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_bytes(b"print('hello')\n")
        (root / "README.md").write_bytes(b"# Example\n")
        subprocess.run(["git", "-C", str(root), "add", "src/main.py", "README.md"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()

    def test_reads_exact_files_from_pinned_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            commit = self._repo(root)
            # A dirty working-tree change must not affect the pinned snapshot.
            (root / "src" / "main.py").write_bytes(b"changed")
            result = GitRepositorySnapshotReader().read_commit(root, commit)
            self.assertEqual(result.commit_sha, commit)
            self.assertEqual(result.files["src/main.py"], b"print('hello')\n")
            self.assertEqual(result.snapshot, RepositorySnapshotBuilder().build(result.files))

    def test_rejects_branch_names_and_short_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            self._repo(root)
            with self.assertRaisesRegex(GitSnapshotError, "full_hex"):
                GitRepositorySnapshotReader().read_commit(root, "HEAD")

    def test_rejects_symlinks_and_submodules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            commit = self._repo(root)
            (root / "link").symlink_to("README.md")
            subprocess.run(["git", "-C", str(root), "add", "link"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "symlink"], check=True)
            symlink_commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            with self.assertRaisesRegex(GitSnapshotError, "non_regular"):
                GitRepositorySnapshotReader().read_commit(root, symlink_commit)

    def test_rejects_missing_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            self._repo(root)
            with self.assertRaises(GitSnapshotError):
                GitRepositorySnapshotReader().read_commit(root, "f" * 40)


if __name__ == "__main__":
    unittest.main()
