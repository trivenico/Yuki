import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.engineering_evidence_store import SQLiteEngineeringEvidenceStore
from core.github_check_gate import GitHubCheck, PullRequestEvidence
from core.release_gate import evaluate_release_gate

SHA = "a" * 40


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = SQLiteEngineeringEvidenceStore(str(Path(self.temp_dir.name) / "audit.sqlite3"))
        self.client = Mock()
        self.client.fetch_pull_request_evidence.return_value = PullRequestEvidence(
            number=74,
            state="open",
            draft=False,
            head_sha=SHA,
            base_branch="main",
            checks=(GitHubCheck("unit-tests", "completed", "success", SHA),),
            required_checks=("unit-tests",),
        )

    def call_gate(self, **overrides):
        args = dict(
            client=self.client,
            store=self.store,
            repository="trivenico/Yuki",
            pull_request=74,
            expected_head_sha=SHA,
            required_checks=("unit-tests",),
        )
        args.update(overrides)
        return evaluate_release_gate(**args)

    def test_success_requires_persisted_audit_record(self):
        result = self.call_gate()
        self.assertTrue(result.allowed)
        self.assertEqual(result.decision, "ready-for-required-checks")
        rows = self.store.list_for_pull_request(repository="trivenico/Yuki", pr_number=74)
        self.assertEqual(rows[0]["payload"]["decision"], "ready-for-required-checks")
        self.assertEqual(rows[0]["payload"]["expected_head_sha"], SHA)

    def test_stale_head_blocks_and_is_persisted(self):
        result = self.call_gate(expected_head_sha="b" * 40)
        self.assertFalse(result.allowed)
        self.assertIn("stale_pull_request_head", result.reasons)
        self.assertTrue(result.audit_persisted)

    def test_missing_required_check_blocks_and_is_persisted(self):
        self.client.fetch_pull_request_evidence.return_value = PullRequestEvidence(
            number=74, state="open", draft=False, head_sha=SHA, base_branch="main",
            checks=(), required_checks=("unit-tests",),
        )
        result = self.call_gate()
        self.assertFalse(result.allowed)
        self.assertTrue(any(x.startswith("required_check_missing:") for x in result.reasons))

    def test_github_unavailable_fails_closed_and_records_failure(self):
        self.client.fetch_pull_request_evidence.side_effect = TimeoutError()
        result = self.call_gate()
        self.assertFalse(result.allowed)
        self.assertIn("github_evidence_unavailable_or_invalid", result.reasons)
        self.assertTrue(result.audit_persisted)

    def test_audit_store_failure_blocks_even_when_checks_pass(self):
        broken_store = Mock()
        broken_store.append.side_effect = OSError("disk unavailable")
        result = self.call_gate(store=broken_store)
        self.assertFalse(result.allowed)
        self.assertIn("audit_evidence_persistence_failed", result.reasons)
        self.assertFalse(result.audit_persisted)

    def test_required_approval_needs_trusted_verifier(self):
        result = self.call_gate(require_human_approval=True)
        self.assertFalse(result.allowed)
        self.assertIn("trusted_human_approval_verifier_missing", result.reasons)

    def test_approval_verifier_is_bound_to_expected_sha(self):
        observed = []
        def verifier(owner, repo, pr, sha):
            observed.append((owner, repo, pr, sha))
            return sha == SHA
        result = self.call_gate(require_human_approval=True, approval_verifier=verifier)
        self.assertTrue(result.allowed)
        self.assertEqual(observed, [("trivenico", "Yuki", 74, SHA)])

    def test_invalid_input_is_rejected_before_network_call(self):
        with self.assertRaises(ValueError):
            self.call_gate(expected_head_sha="bad")
        self.client.fetch_pull_request_evidence.assert_not_called()

    def test_verifier_exception_fails_closed(self):
        result = self.call_gate(
            require_human_approval=True,
            approval_verifier=lambda *_: (_ for _ in ()).throw(RuntimeError("no")),
        )
        self.assertFalse(result.allowed)
        self.assertIn("trusted_human_approval_missing_or_invalid", result.reasons)


if __name__ == "__main__":
    unittest.main()
