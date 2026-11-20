import hashlib
import unittest

from automation.approved_workspace_changes import FileChange
from automation.decision_review import EngineeringDecisionReview
from automation.specialist_quality import SynthesisQualityReport
from automation.workspace_change_proposal import prepare_workspace_change_proposal
from automation.workspace_change_impact import analyze_workspace_change_impact


def make_proposal(path="src/core.py", content="VALUE = 2\n"):
    quality = SynthesisQualityReport(
        plan_id="impact-plan", structural_status="structurally_complete",
        expected_findings=1, received_findings=1, coverage_complete=True,
        findings=(), unresolved_subtasks=(),
    )
    review = EngineeringDecisionReview(
        plan_id="impact-plan", quality=quality, disagreements=(),
        review_status="ready_for_human_decision", proposed_decision=None,
        requires_human_review=True, changes_authorized=False,
    )
    old = "VALUE = 1\n"
    change = FileChange(path, hashlib.sha256(old.encode()).hexdigest(), content)
    return prepare_workspace_change_proposal(
        review=review, decision_actor_id="owner",
        human_decision="Analyze this proposed change.", changes=(change,),
    )


class WorkspaceChangeImpactTests(unittest.TestCase):
    def test_finds_direct_and_transitive_importers_and_test_candidates(self):
        proposal = make_proposal()
        sources = {
            "src/core.py": "VALUE = 1\n",
            "src/service.py": "from src.core import VALUE\n",
            "src/api.py": "from src.service import VALUE\n",
            "tests/test_core.py": "from src.core import VALUE\n",
            "tests/test_api.py": "from src.api import VALUE\n",
        }
        result = analyze_workspace_change_impact(proposal, sources)
        self.assertIn("src/service.py", result.direct_importers)
        self.assertIn("src/api.py", result.transitive_importers)
        self.assertIn("tests/test_core.py", result.test_candidates)
        self.assertIn("tests/test_api.py", result.test_candidates)
        self.assertTrue(result.requires_human_review)
        self.assertFalse(result.tests_executed)

    def test_classifies_dependency_configuration_and_security_paths(self):
        proposal = make_proposal("automation/security_policy.py", "SAFE = True\n")
        result = analyze_workspace_change_impact(proposal, {
            "pyproject.toml": "[project]\n",
            "automation/security_policy.py": "SAFE = False\n",
        })
        self.assertEqual(result.security_sensitive_paths, ("automation/security_policy.py",))
        config_proposal = make_proposal("pyproject.toml", "[project]\nversion='1'\n")
        config_result = analyze_workspace_change_impact(config_proposal, {
            "pyproject.toml": "[project]\n",
        })
        self.assertEqual(config_result.configuration_paths, ("pyproject.toml",))
        self.assertEqual(config_result.dependency_paths, ("pyproject.toml",))

    def test_reports_syntax_errors_without_importing_or_executing(self):
        result = analyze_workspace_change_impact(
            make_proposal(), {"broken.py": "def broken(:\n"}
        )
        self.assertEqual(result.parse_errors, ("broken.py",))

    def test_rejects_oversized_snapshot(self):
        with self.assertRaises(ValueError):
            analyze_workspace_change_impact(
                make_proposal(), {"large.py": "x" * (256 * 1024 + 1)}
            )

    def test_rejects_non_text_snapshot(self):
        with self.assertRaises(ValueError):
            analyze_workspace_change_impact(make_proposal(), {"src/other.py": None})


if __name__ == "__main__":
    unittest.main()
