import json
import tempfile
import unittest
from pathlib import Path

from core.self_evolution import SelfEvolutionInputError, load_and_validate_proposal, self_improvement_status


class SelfEvolutionInputTests(unittest.TestCase):
    def test_loads_and_validates_workspace_proposal_without_applying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposal.json"
            path.write_text(json.dumps({
                "title": "Improve docs",
                "rationale": "Clarify startup behavior.",
                "files": [{"path": "docs/startup.md", "content": "# Startup\n"}],
            }), encoding="utf-8")
            proposal = load_and_validate_proposal(directory, "proposal.json")
        self.assertEqual(proposal.status, "proposal_only")
        self.assertFalse(proposal.requires_human_approval)
        self.assertEqual(len(proposal.sha256), 64)

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SelfEvolutionInputError):
                load_and_validate_proposal(directory, "../proposal.json")

    def test_rejects_unknown_json_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "proposal.json").write_text(
                json.dumps({"title": "x", "rationale": "y", "files": [], "apply": True}),
                encoding="utf-8",
            )
            with self.assertRaises(SelfEvolutionInputError):
                load_and_validate_proposal(directory, "proposal.json")

    def test_status_does_not_claim_autonomous_deployment(self):
        status = self_improvement_status()
        self.assertEqual(status["automatic_commit_merge_deploy"], "disabled")
        self.assertIn("blocked", status["production_runtime"])


if __name__ == "__main__":
    unittest.main()
