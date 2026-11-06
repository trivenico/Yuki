import dataclasses
import hashlib
import unittest

from automation.upgrade_execution_contracts import ExecutionEvidence, ExecutionRequest


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ExecutionRequestTests(unittest.TestCase):
    def make_request(self, **overrides):
        values = dict(
            proposal_id="proposal-123",
            snapshot_digest=digest("snapshot"),
            snapshot_object_digest=digest("object"),
            snapshot_version="version-4",
            test_targets=("tests.test_alpha", "tests.test_beta"),
            runner_kind="container",
            runner_image_digest=digest("runner-image"),
            architecture="linux-amd64",
            runtime_version="python-3.12",
            policy_digest=digest("policy"),
            execution_id="exec-123",
            idempotency_key="idem-123",
            requesting_principal="requester-1",
            reviewer="reviewer-1",
            approval_id="approval-1",
        )
        values.update(overrides)
        return ExecutionRequest.create(**values)

    def test_request_is_canonical_and_order_independent(self):
        first = self.make_request()
        second = self.make_request(test_targets=("tests.test_beta", "tests.test_alpha"))
        self.assertEqual(first.test_targets, second.test_targets)
        self.assertEqual(first.test_plan_digest, second.test_plan_digest)
        self.assertEqual(first.request_digest, second.request_digest)

    def test_request_digest_binds_every_execution_boundary(self):
        original = self.make_request()
        changed = self.make_request(policy_digest=digest("different policy"))
        self.assertNotEqual(original.request_digest, changed.request_digest)
        self.assertNotEqual(original.request_digest, self.make_request(
            runner_image_digest=digest("different image")).request_digest)

    def test_rejects_unsafe_or_ambiguous_test_targets(self):
        for target in ("../tests.test_alpha", "tests;os.system", "tests.test_alpha\n", ""):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.make_request(test_targets=(target,))

    def test_rejects_duplicate_targets(self):
        with self.assertRaisesRegex(ValueError, "test_targets_duplicate"):
            self.make_request(test_targets=("tests.test_alpha", "tests.test_alpha"))

    def test_rejects_unpinned_runner_and_mismatched_reviewer(self):
        with self.assertRaisesRegex(ValueError, "runner_image_digest_invalid"):
            self.make_request(runner_image_digest="latest")
        with self.assertRaisesRegex(ValueError, "reviewer_must_be_independent"):
            self.make_request(requesting_principal="reviewer-1")

    def test_rejects_unsupported_runner_and_schema(self):
        with self.assertRaisesRegex(ValueError, "runner_kind_unsupported"):
            self.make_request(runner_kind="local-shell")
        request = self.make_request()
        with self.assertRaisesRegex(ValueError, "schema_version_unsupported"):
            dataclasses.replace(request, schema_version=2)


class ExecutionEvidenceTests(unittest.TestCase):
    def make_request(self):
        return ExecutionRequest.create(
            proposal_id="proposal-123",
            snapshot_digest=digest("snapshot"),
            snapshot_object_digest=digest("object"),
            snapshot_version="version-4",
            test_targets=("tests.test_alpha",),
            runner_kind="container",
            runner_image_digest=digest("runner-image"),
            architecture="linux-amd64",
            runtime_version="python-3.12",
            policy_digest=digest("policy"),
            execution_id="exec-123",
            idempotency_key="idem-123",
            requesting_principal="requester-1",
            reviewer="reviewer-1",
            approval_id="approval-1",
        )

    def make_evidence(self, request, **overrides):
        values = dict(
            execution_id=request.execution_id,
            request_digest=request.request_digest,
            proposal_id=request.proposal_id,
            snapshot_digest=request.snapshot_digest,
            test_plan_digest=request.test_plan_digest,
            runner_image_digest=request.runner_image_digest,
            policy_digest=request.policy_digest,
            outcome="passed",
            exit_code=0,
            started_at="2026-11-06T10:00:00Z",
            finished_at="2026-11-06T10:01:00Z",
            result_digest=digest("result"),
            runner_identity="https://token.actions.githubusercontent.com/workflows/yuki",
            issuer="https://token.actions.githubusercontent.com",
            audience="yuki-upgrade-verifier",
            nonce="nonce-123",
            key_id="key-1",
            signature="signature-bytes",
        )
        values.update(overrides)
        return ExecutionEvidence(**values)

    def test_evidence_binds_to_exact_request(self):
        request = self.make_request()
        evidence = self.make_evidence(request)
        evidence.verify_binding(request)
        self.assertEqual(len(evidence.payload_digest), 64)

    def test_evidence_rejects_cross_execution_replay(self):
        request = self.make_request()
        evidence = self.make_evidence(request, execution_id="exec-other")
        with self.assertRaisesRegex(ValueError, "evidence_request_binding_mismatch"):
            evidence.verify_binding(request)

    def test_evidence_rejects_invalid_claims(self):
        request = self.make_request()
        with self.assertRaisesRegex(ValueError, "outcome_invalid"):
            self.make_evidence(request, outcome="success")
        with self.assertRaisesRegex(ValueError, "signature_invalid"):
            self.make_evidence(request, signature="")

    def test_signature_is_not_claimed_as_verified(self):
        request = self.make_request()
        evidence = self.make_evidence(request)
        self.assertEqual(evidence.signature, "signature-bytes")
        # This contract intentionally exposes claims only; a trusted verifier
        # must authenticate the signature and issuer/audience independently.


if __name__ == "__main__":
    unittest.main()
