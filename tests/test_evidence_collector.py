import unittest
from types import SimpleNamespace

from automation.approved_workspace_changes import ChangeResult, TestRunResult
from automation.engineering_workflow import EngineeringWorkflowResult
from automation.evidence_collector import (
    EvidenceCollectionError, collect_engineering_evidence,
)


class EvidenceCollectorTests(unittest.TestCase):
    def result(self, exit_code=0, timed_out=False, actor="agent"):
        return EngineeringWorkflowResult(
            workflow_id="eng_123",
            inspection_report="read-only inspection",
            change_result=ChangeResult(
                "change-1", actor, ("src/app.py",), "a" * 64
            ),
            test_result=TestRunResult(
                "test_app", exit_code, timed_out, 0.25, "OK", ""
            ),
            outcome="succeeded" if exit_code == 0 and not timed_out else "tests_failed",
        )

    def test_collects_digest_bound_evidence_from_workflow_result(self):
        bundle = collect_engineering_evidence(
            self.result(), actor_id="agent", commit_sha="a" * 40
        )
        self.assertTrue(bundle.tests_passed)
        self.assertEqual(bundle.actor_id, "agent")
        self.assertEqual(bundle.commit_sha, "a" * 40)
        self.assertEqual(len(bundle.evidence_sha256), 64)
        self.assertEqual(len(bundle.evidence), 2)

    def test_failed_or_timed_out_tests_are_not_passed(self):
        for result in (self.result(exit_code=1), self.result(timed_out=True)):
            bundle = collect_engineering_evidence(
                result, actor_id="agent", commit_sha="b" * 40
            )
            self.assertFalse(bundle.tests_passed)

    def test_inconsistent_outcome_and_nonfinite_duration_are_rejected(self):
        result = self.result(exit_code=1)
        inconsistent = EngineeringWorkflowResult(
            result.workflow_id, result.inspection_report, result.change_result,
            result.test_result, "succeeded",
        )
        with self.assertRaises(EvidenceCollectionError):
            collect_engineering_evidence(
                inconsistent, actor_id="agent", commit_sha="a" * 40
            )
        bad_duration = EngineeringWorkflowResult(
            result.workflow_id, result.inspection_report, result.change_result,
            TestRunResult("test_app", 1, False, float("nan"), "", ""),
            "tests_failed",
        )
        with self.assertRaises(EvidenceCollectionError):
            collect_engineering_evidence(
                bad_duration, actor_id="agent", commit_sha="a" * 40
            )

    def test_actor_mismatch_and_invalid_commit_are_rejected(self):
        with self.assertRaises(EvidenceCollectionError):
            collect_engineering_evidence(
                self.result(), actor_id="other", commit_sha="a" * 40
            )
        with self.assertRaises(EvidenceCollectionError):
            collect_engineering_evidence(
                self.result(), actor_id="agent", commit_sha="not-a-sha"
            )


if __name__ == "__main__":
    unittest.main()
