import tempfile
import unittest
from pathlib import Path

from core.self_improvement import ProposedFile, validate_change_proposal
from core.proposal_staging import ProposalStagingError, stage_change_proposal


class ProposalStagingTests(unittest.TestCase):
    def test_stages_files_outside_active_checkout_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "main.py"
            active.write_text("print('live')\n", encoding="utf-8")
            proposal = validate_change_proposal(
                title="Improve tests", rationale="Add a focused regression test.",
                files=[ProposedFile("tests/test_example.py", "def test_example(): pass\n")],
            )
            staged = stage_change_proposal(proposal=proposal, workspace=root)
            self.assertTrue(staged.name.startswith("yuki-improvement-"))
            self.assertEqual((staged / "tests/test_example.py").read_text(encoding="utf-8"),
                             "def test_example(): pass\n")
            self.assertEqual(active.read_text(encoding="utf-8"), "print('live')\n")
            self.assertTrue((staged / "PROPOSAL.txt").is_file())

    def test_rejects_missing_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            proposal = validate_change_proposal(
                title="Test", rationale="Test proposal.",
                files=[ProposedFile("test_x.py", "pass\n")],
            )
            with self.assertRaises(ProposalStagingError):
                stage_change_proposal(proposal=proposal, workspace=Path(directory) / "missing")

    def test_stages_each_proposal_into_a_unique_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            proposal = validate_change_proposal(
                title="Test", rationale="Test proposal.",
                files=[ProposedFile("README.md", "test\n")],
            )
            first = stage_change_proposal(proposal=proposal, workspace=directory)
            second = stage_change_proposal(proposal=proposal, workspace=directory)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
