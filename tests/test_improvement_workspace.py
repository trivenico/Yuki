import os
from pathlib import Path
import tempfile
import unittest

from core.improvement_workspace import (
    WorkspaceStagingError,
    remove_staged_workspace,
    stage_change_proposal,
)
from core.self_improvement import ProposedFile, validate_change_proposal


class ImprovementWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name) / "jobs"
        self.parent.mkdir()

    def proposal(self, path="docs/notes.md", content="review only\n"):
        return validate_change_proposal(
            title="Improve docs",
            rationale="Stage a reviewable proposal in isolation.",
            files=[ProposedFile(path, content)],
        )

    def test_stages_files_without_touching_repository_paths(self):
        proposal = self.proposal()
        workspace = stage_change_proposal(proposal, parent_dir=self.parent)
        self.addCleanup(lambda: remove_staged_workspace(workspace, parent_dir=self.parent))
        self.assertEqual((workspace / "docs" / "notes.md").read_text(encoding="utf-8"), "review only\n")
        self.assertFalse((self.parent / "docs" / "notes.md").exists())
        if os.name == "posix":
            self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
            self.assertEqual((workspace / "docs" / "notes.md").stat().st_mode & 0o777, 0o600)

    def test_protected_change_requires_matching_approval_digest(self):
        proposal = self.proposal("core/agent.py", "proposed change")
        with self.assertRaisesRegex(WorkspaceStagingError, "exact proposal digest"):
            stage_change_proposal(proposal, parent_dir=self.parent)
        with self.assertRaises(WorkspaceStagingError):
            stage_change_proposal(proposal, parent_dir=self.parent, approved_sha256="wrong")
        workspace = stage_change_proposal(
            proposal, parent_dir=self.parent, approved_sha256=proposal.sha256
        )
        remove_staged_workspace(workspace, parent_dir=self.parent)

    def test_tampered_proposal_digest_is_rejected(self):
        proposal = self.proposal()
        object.__setattr__(proposal, "sha256", "0" * 64)
        with self.assertRaisesRegex(WorkspaceStagingError, "recorded digest"):
            stage_change_proposal(proposal, parent_dir=self.parent)

    def test_symlink_parent_is_rejected(self):
        target = Path(self.tmp.name) / "target"
        target.mkdir()
        link = Path(self.tmp.name) / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable on this runner")
        with self.assertRaises(WorkspaceStagingError):
            stage_change_proposal(self.proposal(), parent_dir=link)

    def test_rejects_symlinked_managed_workspace_for_cleanup(self):
        outside = Path(self.tmp.name) / "yuki-improvement-outside"
        outside.mkdir()
        link = self.parent / "yuki-improvement-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable on this runner")
        with self.assertRaises(WorkspaceStagingError):
            remove_staged_workspace(link, parent_dir=self.parent)
        self.assertTrue(outside.exists())

    def test_cleanup_only_accepts_direct_managed_child(self):
        workspace = stage_change_proposal(self.proposal(), parent_dir=self.parent)
        remove_staged_workspace(workspace, parent_dir=self.parent)
        self.assertFalse(workspace.exists())
        unrelated = Path(self.tmp.name) / "unrelated"
        unrelated.mkdir()
        with self.assertRaises(WorkspaceStagingError):
            remove_staged_workspace(unrelated, parent_dir=self.parent)


if __name__ == "__main__":
    unittest.main()
