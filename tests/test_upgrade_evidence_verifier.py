import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation.upgrade_execution_contracts import ExecutionEvidence
from automation.upgrade_evidence_verifier import (
    Ed25519EvidenceVerifier,
    EvidenceVerificationError,
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class Ed25519EvidenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private_key = Ed25519PrivateKey.generate()
        self.public_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        self.verifier = Ed25519EvidenceVerifier(
            Path(self.temp.name) / "nonces.sqlite3",
            trusted_public_keys={"test-key-1": self.public_bytes},
            allowed_issuers={"https://ci.example"},
            allowed_audiences={"yuki-upgrade-verifier"},
            allowed_runner_identities={"https://ci.example/workflows/yuki-upgrade"},
            max_age_seconds=900,
            clock_skew_seconds=30,
        )

    def evidence(self, **overrides):
        values = dict(
            execution_id="execution-1",
            request_digest=digest("request"),
            proposal_id="proposal-1",
            snapshot_digest=digest("snapshot"),
            test_plan_digest=digest("test-plan"),
            runner_image_digest=digest("runner-image"),
            policy_digest=digest("policy"),
            outcome="passed",
            exit_code=0,
            started_at=(self.now - timedelta(minutes=2)).isoformat(),
            finished_at=(self.now - timedelta(minutes=1)).isoformat(),
            result_digest=digest("result"),
            runner_identity="https://ci.example/workflows/yuki-upgrade",
            issuer="https://ci.example",
            audience="yuki-upgrade-verifier",
            nonce="nonce-1",
            key_id="test-key-1",
            signature="",
        )
        values.update(overrides)
        unsigned = ExecutionEvidence(**values)
        signature = base64.b64encode(
            self.private_key.sign(canonical(unsigned.signed_payload()))
        ).decode("ascii")
        values["signature"] = signature
        return ExecutionEvidence(**values)

    def test_accepts_valid_signature_and_returns_payload_digest(self):
        evidence = self.evidence()
        verified = self.verifier.verify(evidence, now=self.now)
        self.assertEqual(verified, evidence.payload_digest)

    def test_rejects_replayed_nonce(self):
        evidence = self.evidence()
        self.verifier.verify(evidence, now=self.now)
        with self.assertRaisesRegex(EvidenceVerificationError, "evidence_nonce_replayed"):
            self.verifier.verify(evidence, now=self.now)

    def test_rejects_tampered_signed_claim(self):
        evidence = self.evidence()
        tampered = ExecutionEvidence(**{
            **evidence.__dict__,
            "outcome": "failed",
        })
        with self.assertRaisesRegex(EvidenceVerificationError, "evidence_signature_invalid"):
            self.verifier.verify(tampered, now=self.now)

    def test_rejects_untrusted_issuer_audience_runner_and_key(self):
        cases = (
            ({"issuer": "https://attacker.invalid"}, "evidence_issuer_untrusted"),
            ({"audience": "other-service"}, "evidence_audience_mismatch"),
            ({"runner_identity": "unapproved-runner"}, "evidence_runner_identity_untrusted"),
            ({"key_id": "unknown-key"}, "evidence_signing_key_untrusted"),
        )
        for changes, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(EvidenceVerificationError, error):
                self.verifier.verify(self.evidence(**changes), now=self.now)

    def test_rejects_expired_or_timezone_naive_timestamps(self):
        expired = self.evidence(
            started_at=(self.now - timedelta(hours=2)).isoformat(),
            finished_at=(self.now - timedelta(hours=2)).isoformat(),
        )
        with self.assertRaisesRegex(EvidenceVerificationError, "evidence_expired"):
            self.verifier.verify(expired, now=self.now)
        naive = self.evidence(started_at="2026-11-06T11:00:00",
                              finished_at="2026-11-06T11:01:00")
        with self.assertRaisesRegex(EvidenceVerificationError, "evidence_timestamp_timezone_required"):
            self.verifier.verify(naive, now=self.now)

    def test_rejects_invalid_signature_encoding(self):
        evidence = self.evidence()
        evidence = ExecutionEvidence(**{**evidence.__dict__, "signature": "not-base64"})
        with self.assertRaisesRegex(EvidenceVerificationError, "evidence_signature_encoding_invalid"):
            self.verifier.verify(evidence, now=self.now)


if __name__ == "__main__":
    unittest.main()
