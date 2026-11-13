"""Security tests for the fail-closed provider receipt verifier registry."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from core.provider_receipt_verification import (
    ProviderReceiptVerificationError,
    ProviderReceiptVerifierRegistry,
    ReceiptVerificationRequest,
    VerifiedProviderReceipt,
)


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
ACTION_DIGEST = "a" * 64
EVIDENCE_DIGEST = "b" * 64


class FakeTrustedVerifier:
    def __init__(self, mutate=None, error=None):
        self.mutate = mutate
        self.error = error
        self.calls = 0

    def verify(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        receipt = VerifiedProviderReceipt(
            provider_id=request.provider_id,
            owner_id=request.owner_id,
            session_id=request.session_id,
            pending_id=request.pending_id,
            operation_id=request.operation_id,
            action_digest=request.action_digest,
            receipt_id="receipt-123",
            resolution="completed",
            evidence_digest=EVIDENCE_DIGEST,
            verified_at=NOW.isoformat(),
            authority="authenticated_provider_api",
        )
        return self.mutate(receipt) if self.mutate else receipt


def make_request(**changes):
    values = dict(
        provider_id="acme_payments",
        owner_id="owner-1",
        session_id="session-1",
        pending_id="pending-1",
        operation_id="operation-1",
        action_digest=ACTION_DIGEST,
        evidence_reference="provider-operation:operation-1",
    )
    values.update(changes)
    return ReceiptVerificationRequest(**values)


class ProviderReceiptVerificationTests(unittest.TestCase):
    def setUp(self):
        self.verifier = FakeTrustedVerifier()
        self.registry = ProviderReceiptVerifierRegistry(
            {"acme_payments": self.verifier},
            max_receipt_age_seconds=3600,
            clock=lambda: NOW,
        )

    def test_valid_authenticated_provider_receipt_is_accepted(self):
        receipt = self.registry.verify(make_request())
        self.assertEqual(receipt.resolution, "completed")
        self.assertEqual(receipt.owner_id, "owner-1")
        self.assertEqual(self.verifier.calls, 1)

    def test_unknown_provider_fails_closed_without_fallback(self):
        with self.assertRaises(ProviderReceiptVerificationError):
            self.registry.verify(make_request(provider_id="unknown"))
        self.assertEqual(self.verifier.calls, 0)

    def test_provider_timeout_or_auth_failure_fails_closed(self):
        registry = ProviderReceiptVerifierRegistry(
            {"acme_payments": FakeTrustedVerifier(error=TimeoutError())},
            clock=lambda: NOW,
        )
        with self.assertRaises(ProviderReceiptVerificationError):
            registry.verify(make_request())

    def test_wrong_owner_session_pending_operation_or_action_is_rejected(self):
        mutations = (
            {"owner_id": "other-owner"},
            {"session_id": "other-session"},
            {"pending_id": "other-pending"},
            {"operation_id": "other-operation"},
            {"action_digest": "c" * 64},
            {"provider_id": "other_provider"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                registry = ProviderReceiptVerifierRegistry(
                    {"acme_payments": FakeTrustedVerifier(
                        mutate=lambda receipt, m=mutation: replace(receipt, **m)
                    )},
                    clock=lambda: NOW,
                )
                with self.assertRaises(ProviderReceiptVerificationError):
                    registry.verify(make_request())

    def test_unverified_authority_and_malformed_evidence_are_rejected(self):
        mutations = (
            {"authority": "owner_attestation"},
            {"evidence_digest": "not-a-digest"},
            {"receipt_id": ""},
            {"resolution": "retry"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                registry = ProviderReceiptVerifierRegistry(
                    {"acme_payments": FakeTrustedVerifier(
                        mutate=lambda receipt, m=mutation: replace(receipt, **m)
                    )},
                    clock=lambda: NOW,
                )
                with self.assertRaises(ProviderReceiptVerificationError):
                    registry.verify(make_request())

    def test_stale_and_future_receipts_are_rejected(self):
        for timestamp in (
            (NOW - timedelta(hours=2)).isoformat(),
            (NOW + timedelta(minutes=2)).isoformat(),
        ):
            registry = ProviderReceiptVerifierRegistry(
                {"acme_payments": FakeTrustedVerifier(
                    mutate=lambda receipt, ts=timestamp: replace(receipt, verified_at=ts)
                )},
                max_receipt_age_seconds=3600,
                clock=lambda: NOW,
            )
            with self.assertRaises(ProviderReceiptVerificationError):
                registry.verify(make_request())

    def test_request_rejects_untrusted_or_malformed_identity(self):
        with self.assertRaises(ValueError):
            self.registry.verify(make_request(action_digest="not-a-digest"))
        with self.assertRaises(ValueError):
            self.registry.verify(make_request(provider_id="../provider"))

    def test_provider_registry_requires_explicit_valid_adapters(self):
        with self.assertRaises(TypeError):
            ProviderReceiptVerifierRegistry({"acme_payments": object()})
        with self.assertRaises(ValueError):
            ProviderReceiptVerifierRegistry({}, max_receipt_age_seconds=0)


if __name__ == "__main__":
    unittest.main()
