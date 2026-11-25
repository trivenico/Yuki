import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.authorization_decision_receipts import (
    Ed25519AuthorizationAuditEvidenceVerifier,
    SignedAuthorizationDecisionReceipt,
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

    def test_accepts_valid_independently_sourced_signed_receipt(self):
        verifier = self.verifier((self.signed_receipt(),))
        self.assertTrue(verifier.verify_batch(None, None, (self.event,)))

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
