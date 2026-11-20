import unittest

from automation.specialist_quality import assess_synthesis_quality
from automation.specialist_synthesis import PlanSynthesis, SpecialistFinding


def finding(subtask="research", task="task-1", evidence="supplied_material_only", verified=False):
    return SpecialistFinding(
        subtask_id=subtask, task_id=task, specialist="researcher",
        output="Finding from supplied input.", evidence_basis=evidence,
        verified_externally=verified,
    )


class SpecialistQualityTests(unittest.TestCase):
    def test_complete_traceable_synthesis_passes_structural_gate_only(self):
        report = assess_synthesis_quality(PlanSynthesis(
            plan_id="plan-1", status="complete", findings=(finding(),),
            incomplete_subtasks=(), failed_subtasks=(),
        ))
        self.assertEqual(report.structural_status, "structurally_complete")
        self.assertTrue(report.coverage_complete)
        self.assertFalse(report.semantic_correctness_assessed)
        self.assertFalse(report.conflict_resolution_performed)
        self.assertTrue(report.requires_human_review)
        self.assertIn("external_verification_not_performed", report.findings[0].review_reasons)

    def test_incomplete_plan_fails_coverage_gate(self):
        report = assess_synthesis_quality(PlanSynthesis(
            plan_id="plan-2", status="incomplete", findings=(),
            incomplete_subtasks=("write",), failed_subtasks=(),
        ))
        self.assertEqual(report.structural_status, "needs_review")
        self.assertFalse(report.coverage_complete)
        self.assertEqual(report.unresolved_subtasks, ("write",))

    def test_empty_complete_plan_does_not_pass_vacuously(self):
        report = assess_synthesis_quality(PlanSynthesis(
            plan_id="plan-empty", status="complete", findings=(),
            incomplete_subtasks=(), failed_subtasks=(),
        ))
        self.assertEqual(report.structural_status, "needs_review")
        self.assertFalse(report.coverage_complete)

    def test_duplicate_sources_are_flagged(self):
        report = assess_synthesis_quality(PlanSynthesis(
            plan_id="plan-3", status="complete",
            findings=(finding("same", "task-1"), finding("same", "task-2")),
            incomplete_subtasks=(), failed_subtasks=(),
        ))
        self.assertEqual(report.structural_status, "needs_review")
        self.assertIn("duplicate_subtask_finding", report.findings[1].review_reasons)

    def test_conflicting_unresolved_and_completed_subtask_is_rejected(self):
        report = assess_synthesis_quality(PlanSynthesis(
            plan_id="plan-4", status="incomplete", findings=(finding(),),
            incomplete_subtasks=("research",), failed_subtasks=(),
        ))
        self.assertEqual(report.structural_status, "needs_review")

    def test_rejects_unrecognized_synthesis(self):
        with self.assertRaises(TypeError):
            assess_synthesis_quality({"status": "complete"})


if __name__ == "__main__":
    unittest.main()
