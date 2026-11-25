import unittest
from datetime import datetime, timezone

from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.authorization_decision_receipts import (
    Ed25519AuthorizationAuditEvidenceVerifier,
    SignedAuthorizationDecisionReceipt,
    VerifiedAuthorizationDecisionEvidenceRecorder,
    authorization_audit_value_digest,
    authorization_decision_digest,
)


class ReceiptSource:
    def __init__(self, receipts):
        self.receipts = tuple(receipts)
        self.requested = []

    def fetch_receipts(self, decision_hashes):
        self.requested.append(decision_hashes)
        return self.receipts


class KeyResolver:
    def __init__(self, public_key):
        self.public_key = public_key

    def public_key_bytes(self, issuer_key_id):
        if issuer_key_id != "policy-issuer-2026":
            raise ValueError("issuer_key_unknown_or_revoked")
        return self.public_key


class Ed25519AuthorizationAuditEvidenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.event = {
            "event_id": 12,
            "occurred_at": "2026-11-25T10:00:00+00:00",
            "principal_hash": "a" * 64,
            "tenant_hash": None,
            "agent_hash": "b" * 64,
            "action": "task:read",
            "resource_hash": "c" * 64,
            "task_hash": "d" * 64,
            "delegation_hash": None,
            "approval_hash": None,
            "risk": "medium",
            "allowed": 1,
            "decision_hash": "e" * 64,
            "policy_revision_hash": "f" * 64,
            "reason_code": "policy_allow",
        }

    def signed_receipt(self, **overrides):
        values = {
            "receipt_id": "receipt-12",
            "issuer_key_id": "policy-issuer-2026",
            "issued_at": "2026-11-25T10:00:00+00:00",
            "decision_hash": self.event["decision_hash"],
            "principal_hash": self.event["principal_hash"],
            "tenant_hash": self.event["tenant_hash"],
            "agent_hash": self.event["agent_hash"],
            "action": self.event["action"],
            "resource_hash": self.event["resource_hash"],
            "task_hash": self.event["task_hash"],
            "delegation_hash": self.event["delegation_hash"],
            "approval_hash": self.event["approval_hash"],
            "risk": self.event["risk"],
            "allowed": self.event["allowed"],
            "policy_revision_hash": self.event["policy_revision_hash"],
            "reason_code": self.event["reason_code"],
            "signature": "placeholder",
        }
        values.update(overrides)
        unsigned = SignedAuthorizationDecisionReceipt(**values)
        import base64
        values["signature"] = base64.urlsafe_b64encode(
            self.private_key.sign(unsigned.signing_bytes())
        ).decode("ascii").rstrip("=")
        return values

    def verifier(self, receipts):
        return Ed25519AuthorizationAuditEvidenceVerifier(
            ReceiptSource(receipts), KeyResolver(self.public_key),
        )

    def test_verified_recorder_reads_back_and_verifies_before_returning(self):
        principal = Principal(
            "alice", "human", tenant_id="tenant-1", agent_id="agent-1",
        )
        request = AuthorizationRequest(
            principal, "task:read", "resource:42", risk="medium",
            task_id="task-12",
        )
        decision = AuthorizationDecision(True, "decision-verified-1", "policy-v1", "allowed")
        decision_hash = authorization_decision_digest(decision.decision_id)
        now = datetime.now(timezone.utc).isoformat()
        values = {
            "receipt_id": "receipt-verified-1",
            "issuer_key_id": "policy-issuer-2026",
            "issued_at": now,
            "decision_hash": decision_hash,
            "principal_hash": authorization_audit_value_digest(principal.principal_id),
            "tenant_hash": authorization_audit_value_digest(principal.tenant_id),
            "agent_hash": authorization_audit_value_digest(principal.agent_id),
            "action": request.action,
            "resource_hash": authorization_audit_value_digest(request.resource),
            "task_hash": authorization_audit_value_digest(request.task_id),
            "delegation_hash": None,
            "approval_hash": None,
            "risk": request.risk,
            "allowed": 1,
            "policy_revision_hash": authorization_audit_value_digest(decision.policy_revision),
            "reason_code": decision.reason_code,
            "signature": "placeholder",
        }
        unsigned = SignedAuthorizationDecisionReceipt(**values)
        import base64
        values["signature"] = base64.urlsafe_b64encode(
            self.private_key.sign(unsigned.signing_bytes())
        ).decode("ascii").rstrip("=")

        class Source:
            def fetch_receipts(self, hashes):
                self.hashes = hashes
                return (values,)

        class Recorder:
            def record_decision(self, received_request, received_decision, *, decision_hash):
                self.args = (received_request, received_decision, decision_hash)
                return "receipt-verified-1"

        source, recorder = Source(), Recorder()
        verified = VerifiedAuthorizationDecisionEvidenceRecorder(
            recorder, source, KeyResolver(self.public_key),
        )
        self.assertEqual(
            verified.record_decision(request, decision, decision_hash=decision_hash),
            "receipt-verified-1",
        )
        self.assertEqual(source.hashes, (decision_hash,))
        self.assertEqual(recorder.args[2], decision_hash)

    def test_verified_recorder_fails_closed_on_bad_readback_signature(self):
        principal = Principal("alice", "human")
        request = AuthorizationRequest(principal, "task:read", "resource:42")
        decision = AuthorizationDecision(True, "decision-verified-2", "policy-v1", "allowed")
        decision_hash = authorization_decision_digest(decision.decision_id)
        values = self.signed_receipt(
            receipt_id="receipt-verified-2",
            issued_at=datetime.now(timezone.utc).isoformat(),
            decision_hash=decision_hash,
            principal_hash=authorization_audit_value_digest(principal.principal_id),
            tenant_hash=None, agent_hash=None, action=request.action,
            resource_hash=authorization_audit_value_digest(request.resource),
            task_hash=None, delegation_hash=None, approval_hash=None,
            risk=request.risk, allowed=1,
            policy_revision_hash=authorization_audit_value_digest(decision.policy_revision),
            reason_code=decision.reason_code,
        )
        values["signature"] = "A" * 86

        class Source:
            def fetch_receipts(self, hashes):
                return (values,)

        class Recorder:
            def record_decision(self, *args, **kwargs):
                return "receipt-verified-2"

        verified = VerifiedAuthorizationDecisionEvidenceRecorder(
            Recorder(), Source(), KeyResolver(self.public_key),
        )
        with self.assertRaisesRegex(ValueError, "signature_invalid"):
            verified.record_decision(request, decision, decision_hash=decision_hash)

    def test_accepts_valid_independently_sourced_signed_receipt(self):
        verifier = self.verifier((self.signed_receipt(),))
        self.assertTrue(verifier.verify_batch(None, None, (self.event,)))

    def test_public_single_receipt_verification_accepts_matching_event(self):
        receipt = SignedAuthorizationDecisionReceipt.from_mapping(self.signed_receipt())
        verifier = self.verifier(())
        self.assertIsNone(verifier.verify_receipt_for_event(receipt, self.event))

    def test_public_single_receipt_verification_rejects_event_mismatch(self):
        receipt = SignedAuthorizationDecisionReceipt.from_mapping(self.signed_receipt())
        verifier = self.verifier(())
        altered = {**self.event, "action": "task:admin"}
        with self.assertRaisesRegex(ValueError, "content_mismatch"):
            verifier.verify_receipt_for_event(receipt, altered)

    def test_rejects_receipt_when_event_fields_do_not_match_signed_decision(self):
        verifier = self.verifier((self.signed_receipt(),))
        altered = {**self.event, "action": "task:admin"}
        self.assertFalse(verifier.verify_batch(None, None, (altered,)))

    def test_rejects_invalid_ed25519_signature(self):
        receipt = self.signed_receipt()
        receipt["signature"] = "A" * 86
        verifier = self.verifier((receipt,))
        with self.assertRaisesRegex(ValueError, "signature_invalid"):
            verifier.verify_batch(None, None, (self.event,))

    def test_rejects_missing_or_duplicate_receipts(self):
        receipt = self.signed_receipt()
        self.assertFalse(self.verifier(()).verify_batch(None, None, (self.event,)))
        duplicate = {**receipt, "receipt_id": "receipt-13"}
        verifier = self.verifier((receipt, duplicate))
        self.assertFalse(verifier.verify_batch(None, None, (self.event,)))

    def test_rejects_unknown_or_revoked_issuer_key(self):
        receipt = self.signed_receipt(issuer_key_id="revoked-key")
        verifier = self.verifier((receipt,))
        with self.assertRaisesRegex(ValueError, "issuer_key_unknown_or_revoked"):
            verifier.verify_batch(None, None, (self.event,))

    def test_receipt_timestamp_must_match_decision_event_time(self):
        receipt = self.signed_receipt(issued_at="2026-11-25T09:00:00+00:00")
        verifier = self.verifier((receipt,))
        with self.assertRaisesRegex(ValueError, "timestamp_invalid"):
            verifier.verify_batch(None, None, (self.event,))

    def test_decision_digest_matches_audit_store_sha256_convention(self):
        import hashlib
        self.assertEqual(
            authorization_decision_digest("decision-12"),
            hashlib.sha256(b"decision-12").hexdigest(),
        )
        self.assertEqual(authorization_audit_value_digest(None), None)
        self.assertEqual(authorization_audit_value_digest("tenant-1"), hashlib.sha256(b"tenant-1").hexdigest())

    def test_rejects_noncanonical_boolean_allowed_value(self):
        receipt = self.signed_receipt(allowed=True)
        verifier = self.verifier((receipt,))
        with self.assertRaisesRegex(ValueError, "metadata_invalid"):
            verifier.verify_batch(None, None, (self.event,))

    def test_rejects_unhashable_risk_without_raw_type_error(self):
        receipt = self.signed_receipt(risk=["medium"])
        verifier = self.verifier((receipt,))
        with self.assertRaisesRegex(ValueError, "metadata_invalid"):
            verifier.verify_batch(None, None, (self.event,))


if __name__ == "__main__":
    unittest.main()
