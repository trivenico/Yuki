import hashlib
import tempfile
import unittest
from pathlib import Path

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolAuthorizationError
from automation.self_upgrade_pipeline import SelfUpgradeProposalBuilder, SelfUpgradeReviewStore
from automation.upgrade_admission import admit_approved_upgrade
from automation.upgrade_execution_store import UpgradeExecutionConflict, UpgradeExecutionStore


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class UpgradeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.review_store = SelfUpgradeReviewStore(root / "reviews.sqlite3")
        self.execution_store = UpgradeExecutionStore(root / "executions.sqlite3")
        self.source_digest = digest("VALUE = 1\n")
        builder = SelfUpgradeProposalBuilder(["src/example.py"])
        self.proposal = builder.build([
            FileChange("src/example.py", self.source_digest, "VALUE = 2\n")
        ])
        self.review_store.record(self.proposal)

    def admit(self, **overrides):
        args = dict(
            review_store=self.review_store,
            execution_store=self.execution_store,
            proposal_id=self.proposal.proposal_id,
            requesting_principal="requester-1",
            reviewer="reviewer-1",
            approval_id="approval-1",
            idempotency_key="idempotency-1",
            execution_id="execution-1",
            test_targets=("tests.test_upgrade_execution_store",),
            runner_kind="container",
            runner_image_digest=digest("pinned-runner"),
            architecture="linux-amd64",
            runtime_version="python-3.11",
            policy_digest=digest("upgrade-policy"),
        )
        args.update(overrides)
        return admit_approved_upgrade(**args)

    def test_pending_proposal_cannot_be_admitted(self):
        with self.assertRaises(ToolAuthorizationError):
            self.admit()

    def test_requester_cannot_self_approve(self):
        self.review_store.review(
            self.proposal.proposal_id, "approve", reviewer="requester-1",
        )
        with self.assertRaisesRegex(ToolAuthorizationError, "independent_reviewer_required"):
            self.admit(reviewer="requester-1")

    def test_only_recorded_reviewer_can_admit(self):
        self.review_store.review(
            self.proposal.proposal_id, "approve", reviewer="reviewer-1",
        )
        with self.assertRaises(ToolAuthorizationError):
            self.admit(reviewer="different-reviewer")

    def test_approved_snapshot_is_admitted_and_idempotent(self):
        self.review_store.review(
            self.proposal.proposal_id, "approve", reviewer="reviewer-1",
        )
        first = self.admit()
        second = self.admit()
        self.assertEqual(first.execution_id, second.execution_id)
        self.assertEqual(first.state, "queued")
        self.assertEqual(first.request_digest, second.request_digest)
        self.assertEqual(first.request_digest, self.execution_store.get("execution-1").request_digest)

    def test_changed_request_cannot_reuse_admission_idempotency_key(self):
        self.review_store.review(
            self.proposal.proposal_id, "approve", reviewer="reviewer-1",
        )
        self.admit()
        with self.assertRaises(UpgradeExecutionConflict):
            self.admit(execution_id="execution-2", runner_image_digest=digest("other-runner"))


if __name__ == "__main__":
    unittest.main()
