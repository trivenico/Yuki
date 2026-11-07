import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation.host_reviewer_signing import HostReviewerSigningKeyRegistry
from automation.reviewer_contracts import ReviewTask, build_review_result
from automation.reviewer_execution import ReviewerExecutionAdapter, ReviewerExecutionError
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_orchestrator import ProvisionedReviewerTask
from automation.reviewer_sessions import ReviewerSessionAuthority
from automation.reviewer_signing import HMACReviewerSigner


class ReviewerExecutionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.digest = "f" * 64
        self.sessions = ReviewerSessionAuthority(
            root / "sessions.sqlite", secret=b"s" * 32,
            authorize=lambda actor, action, digest: (
                actor == "reviewer-a" and digest == self.digest
                and action in {"review:execute", "review:submit"}
            ),
        )
        self.keys = HostReviewerSigningKeyRegistry(
            authorize=lambda actor, action: actor == "reviewer-a" and action in {
                "review:sign", "review:verify_signature"
            }
        )
        self.keys.register(
            reviewer_agent_id="reviewer-a",
            signer=HMACReviewerSigner(key_id="review-key", key=b"k" * 32),
            active_for_signing=True,
        )
        self.ledger = ReviewerTaskLedger(
            root / "ledger.sqlite",
            authorize=lambda actor, action, digest: (
                actor in {"coordinator", "reviewer-a"} and digest == self.digest
                and action in {"review:assign", "review:execute", "review:submit", "review:read"}
            ),
            signer_resolver=self.keys.resolve,
            session_authority=self.sessions,
        )
        self.task = ReviewTask(
            task_id="review-task-1", artifact_sha256=self.digest,
            author_agent_id="author", reviewer_agent_id="reviewer-a",
            check_name="tests", requirements=("Inspect the test evidence.",),
            evidence_references=("artifact://tests",),
        )
        self.ledger.create_tasks(actor_id="coordinator", tasks=(self.task,))
        self.token = self.sessions.issue(
            reviewer_agent_id="reviewer-a", artifact_sha256=self.digest
        )
        self.provisioned = ProvisionedReviewerTask(
            task_id=self.task.task_id, check_name="tests",
            reviewer_agent_id="reviewer-a", session_token=self.token,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_executes_with_only_assigned_evidence_and_submits_signed_result(self):
        seen = {}
        adapter = ReviewerExecutionAdapter(
            self.ledger,
            evidence_reader=lambda ref, digest: b"test-output" if (
                ref == "artifact://tests" and digest == self.digest
            ) else (_ for _ in ()).throw(KeyError(ref)),
        )
        def execute(task, evidence):
            seen["keys"] = tuple(evidence)
            seen["value"] = evidence["artifact://tests"]
            return build_review_result(task, status="passed", summary="Tests reviewed.")
        outcome = adapter.execute(
            provisioned_task=self.provisioned, actor_id="reviewer-a",
            expected_version=0, task=self.task, executor=execute,
        )
        self.assertEqual(outcome.result_status, "passed")
        self.assertFalse(outcome.blocked_by_host)
        self.assertEqual(seen["keys"], ("artifact://tests",))
        record = self.ledger.get(actor_id="coordinator", task_id=self.task.task_id)
        self.assertTrue(record["signature_verified"])
        self.assertEqual(record["state"], "submitted")

    def test_executor_failure_becomes_blocked_signed_result(self):
        adapter = ReviewerExecutionAdapter(
            self.ledger, evidence_reader=lambda ref, digest: b"ok"
        )
        outcome = adapter.execute(
            provisioned_task=self.provisioned, actor_id="reviewer-a",
            expected_version=0, task=self.task,
            executor=lambda task, evidence: (_ for _ in ()).throw(RuntimeError("worker crashed")),
        )
        self.assertEqual(outcome.result_status, "blocked")
        self.assertTrue(outcome.blocked_by_host)
        record = self.ledger.get(actor_id="coordinator", task_id=self.task.task_id)
        self.assertEqual(record["result"]["status"], "blocked")
        self.assertTrue(record["signature_verified"])

    def test_evidence_size_limit_blocks_execution(self):
        adapter = ReviewerExecutionAdapter(
            self.ledger, evidence_reader=lambda ref, digest: b"x" * 20,
            max_reference_bytes=10, max_total_evidence_bytes=20,
        )
        outcome = adapter.execute(
            provisioned_task=self.provisioned, actor_id="reviewer-a",
            expected_version=0, task=self.task,
            executor=lambda task, evidence: build_review_result(
                task, status="passed", summary="Should not run."
            ),
        )
        self.assertEqual(outcome.result_status, "blocked")
        self.assertTrue(outcome.blocked_by_host)

    def test_mismatched_provisioned_task_is_rejected_before_claim(self):
        adapter = ReviewerExecutionAdapter(
            self.ledger, evidence_reader=lambda ref, digest: b"ok"
        )
        wrong = ProvisionedReviewerTask(
            task_id="other-task", check_name="tests",
            reviewer_agent_id="reviewer-a", session_token=self.token,
        )
        with self.assertRaisesRegex(ReviewerExecutionError, "binding_mismatch"):
            adapter.execute(
                provisioned_task=wrong, actor_id="reviewer-a",
                expected_version=0, task=self.task,
                executor=lambda task, evidence: build_review_result(
                    task, status="passed", summary="No."
                ),
            )
        self.assertEqual(
            self.ledger.get(actor_id="coordinator", task_id=self.task.task_id)["state"],
            "assigned",
        )


if __name__ == "__main__":
    unittest.main()
