from unittest import TestCase
from unittest.mock import patch

from automation.reviewer_batch_runner import execute_reviewer_batch
from automation.reviewer_execution import ReviewerExecutionAdapter, ReviewerExecutionOutcome
from automation.reviewer_orchestrator import ProvisionedReviewerTask
from automation.reviewer_contracts import ReviewTask


def _task(task_id, reviewer):
    return ReviewTask(
        task_id=task_id, artifact_sha256="a" * 64,
        author_agent_id="author", reviewer_agent_id=reviewer,
        check_name="tests", requirements=("run tests",),
        evidence_references=(),
    )


class ReviewerBatchRunnerTests(TestCase):
    def test_executes_distinct_tasks_in_order(self):
        adapter = object.__new__(ReviewerExecutionAdapter)
        first = ProvisionedReviewerTask("r1", "tests", "reviewer1", "token1")
        second = ProvisionedReviewerTask("r2", "tests", "reviewer2", "token2")
        items = ((first, _task("r1", "reviewer1"), 1),
                 (second, _task("r2", "reviewer2"), 1))
        outcome = ReviewerExecutionOutcome("r1", "submitted", "passed", "b" * 64, False)
        with patch.object(adapter, "execute", side_effect=[outcome, outcome]) as execute:
            result = execute_reviewer_batch(
                adapter, items=items,
                actor_id_for_reviewer=lambda reviewer: "actor-" + reviewer,
                executor=lambda task, evidence: None,
            )
        self.assertEqual(result.outcomes, (outcome, outcome))
        self.assertEqual(result.failed_task_ids, ())
        self.assertEqual(execute.call_count, 2)

    def test_isolates_task_failure_and_continues(self):
        adapter = object.__new__(ReviewerExecutionAdapter)
        first = ProvisionedReviewerTask("r1", "tests", "reviewer1", "token1")
        second = ProvisionedReviewerTask("r2", "tests", "reviewer2", "token2")
        items = ((first, _task("r1", "reviewer1"), 1),
                 (second, _task("r2", "reviewer2"), 1))
        outcome = ReviewerExecutionOutcome("r2", "submitted", "passed", "b" * 64, False)
        with patch.object(adapter, "execute", side_effect=[RuntimeError("failed"), outcome]):
            result = execute_reviewer_batch(
                adapter, items=items,
                actor_id_for_reviewer=lambda reviewer: "actor-" + reviewer,
                executor=lambda task, evidence: None,
            )
        self.assertEqual(result.failed_task_ids, ("r1",))
        self.assertEqual(result.outcomes, (outcome,))

    def test_rejects_duplicate_tasks_before_execution(self):
        adapter = object.__new__(ReviewerExecutionAdapter)
        provisioned = ProvisionedReviewerTask("r1", "tests", "reviewer1", "token1")
        items = ((provisioned, _task("r1", "reviewer1"), 1),
                 (provisioned, _task("r1", "reviewer1"), 1))
        with patch.object(adapter, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "duplicate_reviewer_batch_task"):
                execute_reviewer_batch(
                    adapter, items=items,
                    actor_id_for_reviewer=lambda reviewer: "actor-" + reviewer,
                    executor=lambda task, evidence: None,
                )
        execute.assert_not_called()
