import unittest

from automation.decision_review import build_decision_review
from automation.specialist_review import ReviewPosition, record_disagreement
from automation.specialist_synthesis import PlanSynthesis, SpecialistFinding


def synthesis():
    return PlanSynthesis(
        plan_id="plan-1", status="complete",
        findings=(
            SpecialistFinding("security", "task-a", "security-reviewer", "Risk is high.", "supplied_material_only", False),
            SpecialistFinding("platform", "task-b", "platform-reviewer", "Risk is low.", "supplied_material_only", False),
        ),
        incomplete_subtasks=(), failed_subtasks=(),
    )


class DecisionReviewTests(unittest.TestCase):
    def test_builds_review_packet_without_deciding_or_authorizing(self):
        packet = build_decision_review(synthesis=synthesis())
        self.assertEqual(packet.review_status, "ready_for_human_decision")
        self.assertIsNone(packet.proposed_decision)
        self.assertTrue(packet.requires_human_review)
        self.assertFalse(packet.changes_authorized)

    def test_explicit_disagreement_blocks_decision_until_human_review(self):
        conflict = record_disagreement(
            plan_id="plan-1", claim_key="migration-risk",
            positions=(
                ReviewPosition("task-a", "security-reviewer", "high", "Threat model has gaps."),
                ReviewPosition("task-b", "platform-reviewer", "low", "Migration is reversible."),
            ),
        )
        packet = build_decision_review(synthesis=synthesis(), disagreements=(conflict,))
        self.assertEqual(packet.review_status, "blocked_for_review")
        self.assertEqual(packet.disagreements, (conflict,))
        self.assertIsNone(packet.proposed_decision)

    def test_rejects_disagreement_from_another_plan(self):
        conflict = record_disagreement(
            plan_id="other-plan", claim_key="migration-risk",
            positions=(
                ReviewPosition("task-a", "security-reviewer", "high", "Reason A"),
                ReviewPosition("task-b", "platform-reviewer", "low", "Reason B"),
            ),
        )
        with self.assertRaises(ValueError):
            build_decision_review(synthesis=synthesis(), disagreements=(conflict,))

    def test_rejects_unknown_source_task(self):
        conflict = record_disagreement(
            plan_id="plan-1", claim_key="migration-risk",
            positions=(
                ReviewPosition("task-a", "security-reviewer", "high", "Reason A"),
                ReviewPosition("task-unknown", "platform-reviewer", "low", "Reason B"),
            ),
        )
        with self.assertRaises(ValueError):
            build_decision_review(synthesis=synthesis(), disagreements=(conflict,))


if __name__ == "__main__":
    unittest.main()
