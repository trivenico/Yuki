"""Tests for the supervised reviewer dispatch worker."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from automation.reviewer_contracts import ReviewTask, ReviewResult
from automation.reviewer_execution import ReviewerExecutionAdapter, ReviewerExecutionOutcome
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_orchestrator import ProvisionedReviewerTask
from automation.reviewer_worker import ReviewerWorker


class ReviewerWorkerTests(unittest.TestCase):
    def test_worker_requires_trusted_components_and_bounded_limits(self):
        with self.assertRaisesRegex(TypeError, "reviewer_task_ledger_required"):
            ReviewerWorker(None, None, worker_id="reviewer-a",
                           credential_resolver=lambda _: None, executor=lambda *_: None)

    def test_worker_claims_executes_and_acknowledges_only_signed_submission(self):
        digest = "a" * 64
        task = ReviewTask(
            task_id="review-task-1", artifact_sha256=digest,
            author_agent_id="author", reviewer_agent_id="reviewer-a",
            check_name="acceptance_criteria",
            requirements=("Check the acceptance criteria.",),
            evidence_references=(),
        )
        provisioned = ProvisionedReviewerTask(
            task_id=task.task_id, check_name=task.check_name,
            reviewer_agent_id="reviewer-a", session_token="session-token",
            lease_token="lease-token", lease_generation=1, review_task=task,
        )
        ledger = Mock(spec=ReviewerTaskLedger)
        ledger._actor.return_value = "reviewer-a"
        ledger.claim_dispatches.return_value = ({
            "dispatch_id": "dispatch-1", "task_id": task.task_id,
            "artifact_sha256": digest, "reviewer_agent_id": "reviewer-a",
            "task": {
                "task_id": task.task_id, "artifact_sha256": digest,
                "author_agent_id": "author", "check_name": task.check_name,
                "requirements": list(task.requirements), "evidence_references": [],
            },
            "claim_token": "x" * 32,
        },)
        ledger.get.side_effect = [
            {"state": "assigned", "version": 0},
            {"state": "submitted", "signature_verified": True},
        ]
        adapter = Mock(spec=ReviewerExecutionAdapter)
        adapter.execute.return_value = ReviewerExecutionOutcome(
            task_id=task.task_id, state="submitted", result_status="passed",
            result_digest="b" * 64, blocked_by_host=False,
        )
        resolver_contexts = []
        worker = ReviewerWorker(
            ledger, adapter, worker_id="reviewer-a",
            credential_resolver=lambda context: (resolver_contexts.append(dict(context)) or provisioned),
            executor=lambda *_: None,
        )
        report = worker.run_once()
        self.assertEqual((report.claimed, report.completed, report.retried, report.deferred), (1, 1, 0, 0))
        self.assertNotIn("claim_token", resolver_contexts[0])
        adapter.execute.assert_called_once()
        ledger.complete_dispatch.assert_called_once()

    def test_worker_does_not_retry_after_task_enters_in_progress(self):
        digest = "a" * 64
        task = ReviewTask(
            task_id="review-task-2", artifact_sha256=digest,
            author_agent_id="author", reviewer_agent_id="reviewer-a",
            check_name="acceptance_criteria",
            requirements=("Check the acceptance criteria.",),
            evidence_references=(),
        )
        provisioned = ProvisionedReviewerTask(
            task_id=task.task_id, check_name=task.check_name,
            reviewer_agent_id="reviewer-a", session_token="session-token",
            lease_token="lease-token", lease_generation=1, review_task=task,
        )
        ledger = Mock(spec=ReviewerTaskLedger)
        ledger._actor.return_value = "reviewer-a"
        ledger.claim_dispatches.return_value = ({
            "dispatch_id": "dispatch-2", "task_id": task.task_id,
            "artifact_sha256": digest, "reviewer_agent_id": "reviewer-a",
            "task": {
                "task_id": task.task_id, "artifact_sha256": digest,
                "author_agent_id": "author", "check_name": task.check_name,
                "requirements": list(task.requirements), "evidence_references": [],
            },
            "claim_token": "y" * 32,
        },)
        ledger.get.return_value = {"state": "in_progress", "version": 1}
        adapter = Mock(spec=ReviewerExecutionAdapter)
        worker = ReviewerWorker(
            ledger, adapter, worker_id="reviewer-a",
            credential_resolver=lambda _: provisioned,
            executor=lambda *_: None,
        )
        report = worker.run_once()
        self.assertEqual(report.deferred, 1)
        adapter.execute.assert_not_called()
        ledger.fail_dispatch.assert_not_called()
        ledger.complete_dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
