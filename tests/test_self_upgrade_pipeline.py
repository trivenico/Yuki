import hashlib
import unittest

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolInputError
from automation.self_upgrade_pipeline import SelfUpgradeProposalBuilder, SelfUpgradeReviewStore, UpgradeProposal
import tempfile
from pathlib import Path


class SelfUpgradeProposalBuilderTests(unittest.TestCase):
    def setUp(self):
        self.source = "VALUE = 1\n"
        self.digest = hashlib.sha256(self.source.encode()).hexdigest()
        self.builder = SelfUpgradeProposalBuilder(["src/example.py", "tests/test_example.py"])

    def test_review_store_persists_and_lists_pending_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            proposal = self.builder.build([
                FileChange("src/example.py", self.digest, "VALUE = 2\\n")
            ])
            row = store.record(proposal)
            self.assertEqual(row["status"], "pending_review")
            self.assertEqual(store.record(proposal)["proposal_id"], proposal.proposal_id)
            self.assertEqual(store.status()["pending_review"], 1)
            reopened = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            self.assertEqual(reopened.list()[0]["proposal_id"], proposal.proposal_id)
            self.assertFalse(store.status()["automatic_application_enabled"])

    def test_approval_only_authorizes_isolated_testing_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            proposal = self.builder.build([
                FileChange("src/example.py", self.digest, "VALUE = 3\\n")
            ])
            store.record(proposal)
            approved = store.review(
                proposal.proposal_id, "approve", reviewer="local-operator",
                note="Review the isolated test result next.",
            )
            self.assertEqual(approved["status"], "approved_for_isolated_testing")
            self.assertEqual(
                store.review(proposal.proposal_id, "approve", reviewer="local-operator")["status"],
                "approved_for_isolated_testing",
            )
            self.assertFalse(store.status()["automatic_promotion_enabled"])

    def test_rejected_proposal_cannot_be_reopened(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            proposal = self.builder.build([
                FileChange("src/example.py", self.digest, "VALUE = 4\\n")
            ])
            store.record(proposal)
            store.review(proposal.proposal_id, "reject", reviewer="local-operator")
            with self.assertRaisesRegex(ValueError, "already_reviewed"):
                store.review(proposal.proposal_id, "approve", reviewer="local-operator")

    def test_review_requires_existing_proposal_and_valid_reviewer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            with self.assertRaisesRegex(ValueError, "not_found"):
                store.review("upgrade_" + "a" * 24, "approve", reviewer="operator")
            with self.assertRaisesRegex(ValueError, "reviewer_invalid"):
                store.review("upgrade_" + "a" * 24, "approve", reviewer=" ")

    def test_review_store_rejects_malformed_proposal_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            malformed = UpgradeProposal(
                proposal_id="upgrade_" + "a" * 24,
                content_digest="b" * 64,
                changed_paths=([ "not-a-string-path" ],),
                patch_bytes=0,
            )
            with self.assertRaisesRegex(ValueError, "invariants"):
                store.record(malformed)

    def test_digest_bound_test_evidence_is_recorded_after_review(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(
                Path(directory) / "reviews.sqlite3",
                runner_attestation_verifier=lambda payload, token: (
                    token == "signed-test-attestation"
                    and payload["runner_kind"] == "github_actions"
                ),
            )
            proposal = self.builder.build([FileChange("src/example.py", self.digest, "VALUE = 7\n")])
            store.record(proposal)
            evidence = dict(
                run_id="test_" + "1" * 24,
                proposal_id=proposal.proposal_id,
                content_digest=proposal.content_digest,
                test_target="tests.test_example",
                outcome="passed",
                exit_code=0,
                runner_kind="github_actions",
                runner_digest="a" * 64,
                result_digest="b" * 64,
                runner_attestation="signed-test-attestation",
            )
            with self.assertRaisesRegex(ValueError, "not_approved"):
                store.record_test_evidence(**evidence)
            store.review(proposal.proposal_id, "approve", reviewer="operator")
            row = store.record_test_evidence(**evidence)
            self.assertEqual(row["content_digest"], proposal.content_digest)
            self.assertEqual(
                row["runner_attestation_digest"],
                hashlib.sha256(evidence["runner_attestation"].encode("utf-8")).hexdigest(),
            )
            self.assertNotIn("runner_attestation", row)
            self.assertEqual(store.record_test_evidence(**evidence)["run_id"], evidence["run_id"])
            self.assertEqual(len(store.list_test_evidence(proposal.proposal_id)), 1)

    def test_test_evidence_rejects_unverified_runner_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(
                Path(directory) / "reviews.sqlite3",
                runner_attestation_verifier=lambda payload, token: False,
            )
            proposal = self.builder.build([
                FileChange("src/example.py", self.digest, "VALUE = 8\n")
            ])
            store.record(proposal)
            store.review(proposal.proposal_id, "approve", reviewer="operator")
            with self.assertRaisesRegex(ValueError, "attestation_verification_failed"):
                store.record_test_evidence(
                    run_id="test_" + "3" * 24,
                    proposal_id=proposal.proposal_id,
                    content_digest=proposal.content_digest,
                    test_target="tests.test_example",
                    outcome="passed",
                    exit_code=0,
                    runner_kind="container",
                    runner_digest="a" * 64,
                    result_digest="b" * 64,
                    runner_attestation="untrusted-token",
                )

    def test_test_evidence_accepts_only_dotted_unittest_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(
                Path(directory) / "reviews.sqlite3",
                runner_attestation_verifier=lambda payload, token: token == "ok",
            )
            proposal = self.builder.build([
                FileChange("src/example.py", self.digest, "VALUE = 9\n")
            ])
            store.record(proposal)
            store.review(proposal.proposal_id, "approve", reviewer="operator")
            for invalid_target in (
                "../tests.test_example", "tests/evil.py", "tests;echo", "tests.",
                "tests.test_example\\nimport os", "", None,
            ):
                with self.subTest(target=invalid_target):
                    with self.assertRaisesRegex(ValueError, "test_target_invalid"):
                        store.record_test_evidence(
                            run_id="test_" + "4" * 24,
                            proposal_id=proposal.proposal_id,
                            content_digest=proposal.content_digest,
                            test_target=invalid_target,
                            outcome="passed",
                            exit_code=0,
                            runner_kind="github_actions",
                            runner_digest="a" * 64,
                            result_digest="b" * 64,
                            runner_attestation="ok",
                        )

    def test_review_store_bounds_list_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            with self.assertRaisesRegex(ValueError, "limit"):
                store.list(limit=201)

    def test_builds_stable_digest_without_applying_changes(self):
        change = FileChange("src/example.py", self.digest, "VALUE = 2\n")
        proposal = self.builder.build([change])
        self.assertTrue(proposal.proposal_id.startswith("upgrade_"))
        self.assertEqual(proposal.changed_paths, ("src/example.py",))
        self.assertTrue(proposal.review_required)
        self.assertTrue(proposal.tests_required)
        self.assertFalse(proposal.promotion_allowed)

    def test_repeated_same_proposal_has_same_digest(self):
        change = FileChange("src/example.py", self.digest, "VALUE = 2\n")
        first = self.builder.build([change])
        second = self.builder.build([change])
        self.assertEqual(first.content_digest, second.content_digest)

    def test_unlisted_path_rejected(self):
        change = FileChange("src/other.py", self.digest, "x=2\n")
        with self.assertRaises(ToolInputError):
            self.builder.build([change])

    def test_builder_rejects_windows_separators_and_traversal_allowlists(self):
        for path in (r"src\\example.py", "src/../secret.py", "src//example.py"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    SelfUpgradeProposalBuilder([path])

    def test_review_store_rejects_backslash_traversal_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SelfUpgradeReviewStore(Path(directory) / "reviews.sqlite3")
            malformed = UpgradeProposal(
                proposal_id="upgrade_" + "c" * 24,
                content_digest="d" * 64,
                changed_paths=(r"src\\..\\secret.py",),
                patch_bytes=1,
            )
            with self.assertRaisesRegex(ValueError, "invariants"):
                store.record(malformed)

    def test_sensitive_path_rejected_even_if_allowlisted(self):
        builder = SelfUpgradeProposalBuilder([".env"])
        change = FileChange(".env", self.digest, "TOKEN=example\n")
        with self.assertRaises(ToolInputError):
            builder.build([change])

    def test_duplicate_paths_rejected(self):
        change = FileChange("src/example.py", self.digest, "VALUE = 2\n")
        with self.assertRaises(ToolInputError):
            self.builder.build([change, change])

    def test_bad_digest_and_nul_content_rejected(self):
        with self.assertRaises(ToolInputError):
            self.builder.build([FileChange("src/example.py", "bad", "x=2\n")])
        with self.assertRaises(ToolInputError):
            self.builder.build([FileChange("src/example.py", self.digest, "x=2\x00")])

    def test_change_count_is_bounded(self):
        changes = [
            FileChange("src/example.py", self.digest, "x=2\n"),
            FileChange("tests/test_example.py", self.digest, "x=3\n"),
        ] * 5
        with self.assertRaises(ToolInputError):
            self.builder.build(changes)


if __name__ == "__main__":
    unittest.main()
