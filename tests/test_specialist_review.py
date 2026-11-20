import unittest

from automation.specialist_review import ReviewPosition, record_disagreement


class SpecialistReviewTests(unittest.TestCase):
    def test_records_explicit_conflict_without_adjudication(self):
        record = record_disagreement(
            plan_id="plan-1", claim_key="database-migration-risk",
            positions=(
                ReviewPosition("task-a", "security-reviewer", "high risk", "Rollback path is untested."),
                ReviewPosition("task-b", "platform-reviewer", "low risk", "Migration is additive and reversible."),
            ),
        )
        self.assertEqual(record.status, "unresolved")
        self.assertTrue(record.requires_human_review)
        self.assertEqual(len(record.positions), 2)

    def test_rejects_identical_positions(self):
        with self.assertRaises(ValueError):
            record_disagreement(
                plan_id="plan-1", claim_key="claim",
                positions=(
                    ReviewPosition("task-a", "reviewer-a", "yes", "Reason A"),
                    ReviewPosition("task-b", "reviewer-b", " YES ", "Reason B"),
                ),
            )

    def test_rejects_duplicate_task_references(self):
        with self.assertRaises(ValueError):
            record_disagreement(
                plan_id="plan-1", claim_key="claim",
                positions=(
                    ReviewPosition("same", "reviewer-a", "yes", "Reason A"),
                    ReviewPosition("same", "reviewer-b", "no", "Reason B"),
                ),
            )

    def test_requires_multiple_specialists(self):
        with self.assertRaises(ValueError):
            record_disagreement(
                plan_id="plan-1", claim_key="claim",
                positions=(
                    ReviewPosition("task-a", "reviewer", "yes", "Reason A"),
                    ReviewPosition("task-b", "reviewer", "no", "Reason B"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
