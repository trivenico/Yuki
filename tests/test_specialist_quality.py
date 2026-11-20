import unittest

from automation.specialist_quality import assess_synthesis_quality
from automation.specialist_synthesis import PlanSynthesis, SpecialistFinding


class SpecialistQualityTests(unittest.TestCase):
    def test_complete_traceable_synthesis_passes_structural_gate_only(self):
        synthesis = PlanSynthesis(
            plan_id="plan-1", status="complete",
            findings=(SpecialistFinding(
                subtask_id="research", task_id="task-1", specialist="researcher",
                output="Finding from supplied input.", evidence_basis="supplied_material_only",
                verified_externally=False,
            ),),
            incomplete_subtasks=(), failed_subtasks=(),
        )
        report = assess_synthesis_quality(synthesis)
        self.assertEqual(report.structural_status, "structurally_complete")
        self.assertTrue(report.coverage_complete)
        self.assertFalse(report.semantic_correctness_assessed)
        self.assertFalse(report.conflict_resolution_performed)
        self.assertTrue(report.requires_human_review)
        self.assertIn("external_verification_not_performed", report.findings[0].review_reasons)

    def test_incomplete_plan_fails_coverage_gate(self):
        synthesis = PlanSynthesis(
            plan_id="plan-2", status="incomplete", findings=(),
            incomplete_subtasks=("write",), failed_subtasks=(),
        )
        report = assess_synthesis_quality(synthesis)
        self.assertEqual(report.structural_status, "needs_review")
        self.assertFalse(report.coverage_complete)
        self.assertEqual(report.unresolved_subtasks, ("write",))

    def test_duplicate_sources_are_flagged(self):
        finding = SpecialistFinding(
            subtask_id="same", task_id="task-1", specialist="reviewer",
            output="A", evidence_basis="supplied_material_only", verified_externally=False,
        )
        second = SpecialistFinding(
            subtask_id="same", task_id="task-2", specialist="reviewer",
            output="B", evidence_basis="supplied_material_only", verified_externally=False,
        )
        report = assess_synthesis_quality(PlanSynthesis(
            plan_id="plan-3", status="complete", findings=(finding, second),
            incomplete_subtasks=(), failed_subtasks=(),
        ))
        self.assertEqual(report.structural_status, "needs_review")
        self.assertIn("duplicate_subtask_finding", report.findings[1].review_reasons)

    def test_rejects_unrecognized_synthesis(self):
        with self.assertRaises(TypeError):
            assess_synthesis_quality({"status": "complete"})


if __name__ == "__main__":
    unittest.main()
