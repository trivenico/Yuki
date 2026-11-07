import subprocess
import tempfile
import unittest
from pathlib import Path

from automation.git_repository_snapshot import GitRepositorySnapshotReader
from automation.reproducible_workspace import GitBackedWorkspaceManager
from automation.verification_evidence_store import VerificationEvidenceStore
from automation.verification_workflow import VerificationWorkflow


class ReproducibleWorkspaceTests(unittest.TestCase):
    def _repo(self, root):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Yuki Tests"], check=True)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_bytes(b"print('snapshot')\n")
        (root / "requirements.txt").write_bytes(b"example==1.2.3\n")
        subprocess.run(["git", "-C", str(root), "add", "src/app.py", "requirements.txt"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()

    def test_captures_artifact_provenance_and_materializes_pinned_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "repo"
            repo.mkdir()
            commit = self._repo(repo)
            evidence = VerificationEvidenceStore(base / "evidence.sqlite3")
            workflow = VerificationWorkflow(
                base / "artifacts.sqlite3", evidence,
                reviewer_authorize=lambda *_: True,
            )
            manager = GitBackedWorkspaceManager(
                repo, base / "workspaces", base / "provenance.sqlite3",
                GitRepositorySnapshotReader(), workflow,
            )
            provenance = manager.capture(
                commit=commit, plan_id="plan-1", subtask_id="subtask-1",
                task_id="task-1", author_agent_id="coding",
                created_at="2026-11-07T10:00:00Z", workspace_name="task-1",
            )
            workspace = Path(provenance.workspace_path)
            self.assertEqual((workspace / "src/app.py").read_bytes(), b"print('snapshot')\n")
            self.assertEqual((workspace / "requirements.txt").read_bytes(), b"example==1.2.3\n")
            self.assertEqual(provenance.dependency_files, ("requirements.txt",))
            self.assertEqual(manager.get_provenance(provenance.artifact_id), provenance)
            self.assertEqual(workflow.read_artifact(provenance.artifact_id)[:1], b"{")

    def test_refuses_workspace_name_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "repo"
            repo.mkdir()
            commit = self._repo(repo)
            workflow = VerificationWorkflow(
                base / "artifacts.sqlite3",
                VerificationEvidenceStore(base / "evidence.sqlite3"),
                reviewer_authorize=lambda *_: True,
            )
            manager = GitBackedWorkspaceManager(
                repo, base / "workspaces", base / "provenance.sqlite3",
                GitRepositorySnapshotReader(), workflow,
            )
            with self.assertRaisesRegex(ValueError, "workspace_name"):
                manager.capture(
                    commit=commit, plan_id="p", subtask_id="s", task_id="t",
                    author_agent_id="coder", created_at="now", workspace_name="../escape",
                )


if __name__ == "__main__":
    unittest.main()
