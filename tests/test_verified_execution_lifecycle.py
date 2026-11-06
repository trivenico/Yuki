import base64
from datetime import datetime, timezone
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation.upgrade_execution_contracts import ExecutionEvidence, ExecutionRequest
from automation.upgrade_execution_store import (
    UpgradeExecutionConflict, UpgradeExecutionStore, UpgradeExecutionTransitionError,
)
from automation.upgrade_evidence_verifier import Ed25519EvidenceVerifier


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


class VerifiedExecutionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = UpgradeExecutionStore(root / "execution.sqlite3")
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.verifier = Ed25519EvidenceVerifier(
            root / "nonces.sqlite3",
            trusted_public_keys={"test-key": public},
            allowed_issuers={"https://ci.example"},
            allowed_audiences={"yuki-upgrade-verifier"},
            allowed_runner_identities={"https://ci.example/workflow/upgrade"},
            max_age_seconds=3600,
        )
        self.now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)\n        self.request = ExecutionRequest.create(
            proposal_id="proposal-verified",
            snapshot_digest=digest("snapshot"),
            snapshot_object_digest=digest("snapshot-object"),
            snapshot_version="snapshot-version-1",
            test_targets=("tests.test_upgrade",),
            runner_kind="container",
            runner_image_digest=digest("runner"),
            architecture="linux-amd64",
            runtime_version="python-3.11",
            policy_digest=digest("policy"),
            execution_id="execution-verified",
            idempotency_key="idem-verified",
            requesting_principal="requester",
            reviewer="reviewer",
            approval_id="approval",
        )

    def signed_evidence(self, *, outcome="passed", exit_code=0, nonce="nonce-verified"):
        claims = dict(
            execution_id=self.request.execution_id,
            request_digest=self.request.request_digest,
            proposal_id=self.request.proposal_id,
            snapshot_digest=self.request.snapshot_digest,
            test_plan_digest=self.request.test_plan_digest,
            runner_image_digest=self.request.runner_image_digest,
            policy_digest=self.request.policy_digest,
            outcome=outcome,
            exit_code=exit_code,
            started_at="2026-11-06T11:00:00+00:00",
            finished_at="2026-11-06T11:01:00+00:00",
            result_digest=digest("runner-result"),
            runner_identity="https://ci.example/workflow/upgrade",
            issuer="https://ci.example",
            audience="yuki-upgrade-verifier",
            nonce=nonce,
            key_id="test-key",
        )
        unsigned = ExecutionEvidence(**claims, signature="placeholder")
        signature = base64.b64encode(
            self.private.sign(canonical(unsigned.signed_payload()))
        ).decode("ascii")
        return ExecutionEvidence(**claims, signature=signature)

    def prepare(self, evidence):
        row = self.store.create(self.request)
        row = self.store.transition(
            row.execution_id, expected_version=row.version, to_state="running",
            actor_id="runner", reason_code="worker_started",
        )
        row = self.store.transition(
            row.execution_id, expected_version=row.version, to_state="evidence_pending",
            actor_id="runner", reason_code="runner_finished",
        )
        return self.store.record_evidence(
            row.execution_id, expected_version=row.version, evidence=evidence,
        )

    def test_valid_passed_evidence_is_required_for_verified_pass(self):
        row = self.prepare(self.signed_evidence())
        final = self.store.verify_and_finalize_evidence(
            row.execution_id, expected_version=row.version, verifier=self.verifier, now=self.now,
        )
        self.assertEqual(final.state, "verified_pass")
        self.assertEqual(final.version, row.version + 2)
        events = self.store.list_events(row.execution_id)
        self.assertEqual([event["to_state"] for event in events],
                         ["queued", "running", "evidence_pending", "evidence_pending",
                          "verifying", "verified_pass"])

    def test_valid_signature_with_failed_test_result_is_verified_fail(self):
        row = self.prepare(self.signed_evidence(outcome="failed", exit_code=1))
        final = self.store.verify_and_finalize_evidence(
            row.execution_id, expected_version=row.version, verifier=self.verifier, now=self.now,
        )
        self.assertEqual(final.state, "verified_fail")

    def test_invalid_signature_is_rejected_not_verified(self):
        evidence = self.signed_evidence()
        invalid = ExecutionEvidence(**{**evidence.__dict__, "signature": base64.b64encode(b"x" * 64).decode()})
        row = self.prepare(invalid)
        final = self.store.verify_and_finalize_evidence(
            row.execution_id, expected_version=row.version, verifier=self.verifier, now=self.now,
        )
        self.assertEqual(final.state, "rejected")
        self.assertNotIn(final.state, {"verified_pass", "verified_fail"})

    def test_cannot_finalize_without_recorded_evidence_or_from_wrong_version(self):
        row = self.store.create(self.request)
        with self.assertRaises(UpgradeExecutionTransitionError):
            self.store.verify_and_finalize_evidence(
                row.execution_id, expected_version=row.version, verifier=self.verifier, now=self.now,
            )
        row = self.store.transition(
            row.execution_id, expected_version=row.version, to_state="running",
            actor_id="runner", reason_code="started",
        )
        row = self.store.transition(
            row.execution_id, expected_version=row.version, to_state="evidence_pending",
            actor_id="runner", reason_code="finished",
        )
        row = self.store.record_evidence(
            row.execution_id, expected_version=row.version,
            evidence=self.signed_evidence(),
        )
        with self.assertRaisesRegex(UpgradeExecutionConflict, "stale_execution_version"):
            self.store.verify_and_finalize_evidence(
                row.execution_id, expected_version=row.version - 1, verifier=self.verifier, now=self.now,
            )


if __name__ == "__main__":
    unittest.main()
