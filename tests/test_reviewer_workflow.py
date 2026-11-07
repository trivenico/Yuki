import unittest
from types import SimpleNamespace

from automation.reviewer_team import ReviewerTeamPlan, ReviewerAssignment
from automation.reviewer_workflow import ReviewerWorkflowError, build_review_tasks


def plan():
    return ReviewerTeamPlan(
        artifact_sha256="a" * 64,
        author_agent_id="builder",
        assignments=(
            ReviewerAssignment("tests", "test-reviewer"),
            ReviewerAssignment("security", "security-reviewer"),
        ),
    )


def requirements():
    return {
        "tests": ("Run the targeted test suite", "Inspect failure output"),
        "security": ("Review authorization boundaries",),
    }


def evidence():
    return {
        "tests": ("snapshot://artifact/tests",),
        "security": ("snapshot://artifact/source",),
    }


class ReviewerWorkflowTests(unittest.TestCase):
    def test_materializes_distinct_artifact_bound_tasks_deterministically(self):
        first = build_review_tasks(
            plan(), requirements_by_check=requirements(),
            evidence_references_by_check=evidence(),
        )
        second = build_review_tasks(
            plan(), requirements_by_check=requirements(),
            evidence_references_by_check=evidence(),
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual({task.reviewer_agent_id for task in first}, {"test-reviewer", "security-reviewer"})
        self.assertTrue(all(task.artifact_sha256 == "a" * 64 for task in first))
        self.assertTrue(all(task.author_agent_id != task.reviewer_agent_id for task in first))

    def test_requires_requirements_for_every_assignment(self):
        with self.assertRaisesRegex(ReviewerWorkflowError, "requirements_must_match"):
            build_review_tasks(
                plan(), requirements_by_check={"tests": ("Run tests",)},
                evidence_references_by_check=evidence(),
            )

    def test_requires_evidence_references_for_every_assignment(self):
        with self.assertRaisesRegex(ReviewerWorkflowError, "evidence_references_must_match"):
            build_review_tasks(
                plan(), requirements_by_check=requirements(),
                evidence_references_by_check={"tests": ("snapshot://tests",)},
            )

    def test_rejects_author_or_reused_reviewer(self):
        invalid = ReviewerTeamPlan(
            artifact_sha256="a" * 64, author_agent_id="builder",
            assignments=(
                ReviewerAssignment("tests", "builder"),
                ReviewerAssignment("security", "builder"),
            ),
        )
        with self.assertRaises(ReviewerWorkflowError):
            build_review_tasks(
                invalid, requirements_by_check=requirements(),
                evidence_references_by_check=evidence(),
            )

    def test_rejects_empty_requirements(self):
        values = requirements()
        values["security"] = ()
        with self.assertRaisesRegex(ReviewerWorkflowError, "invalid_review_task"):
            build_review_tasks(
                plan(), requirements_by_check=values,
                evidence_references_by_check=evidence(),
            )

    def test_rejects_wrong_plan_type(self):
        with self.assertRaisesRegex(TypeError, "reviewer_team_plan_required"):
            build_review_tasks(
                SimpleNamespace(), requirements_by_check=requirements(),
                evidence_references_by_check=evidence(),
            )


if __name__ == "__main__":
    unittest.main()
