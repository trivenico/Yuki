"""Fail-closed verification boundary for external provider receipts.

Provider-specific adapters are trusted host code. They must independently query
an authenticated provider API or validate an authoritative provider signature;
they must never treat a user/model-supplied evidence string as proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Mapping, Protocol


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ReceiptVerificationRequest:
    """Expected immutable identity and action binding for one uncertain operation."""

    provider_id: str
    owner_id: str
    session_id: str
    pending_id: str
    operation_id: str
    action_digest: str
    evidence_reference: str


@dataclass(frozen=True)
class VerifiedProviderReceipt:
    """Claims returned only after a trusted adapter verifies provider authority."""

    provider_id: str
    owner_id: str
    session_id: str
    pending_id: str
    operation_id: str
    action_digest: str
    receipt_id: str
    resolution: str
    evidence_digest: str
    verified_at: str
    authority: str


class ProviderReceiptVerifier(Protocol):
    """Trusted provider adapter contract; implementations perform real verification."""

    def verify(self, request: ReceiptVerificationRequest) -> VerifiedProviderReceipt:
        """Raise on unavailable, unverifiable, stale, or mismatched provider evidence."""
        ...


class ProviderReceiptVerificationError(RuntimeError):
    """Provider evidence could not be authoritatively verified."""


class ProviderReceiptVerifierRegistry:
    """Explicit allow-list of provider verifiers with strict claim binding.

    No default verifier exists. Unknown providers, adapter exceptions, malformed
    claims, and any identity/action mismatch fail closed. This registry does not
    perform HTTP itself; adapters must own their authenticated provider clients.
    """

    def __init__(self, verifiers: Mapping[str, ProviderReceiptVerifier], *,
                 max_receipt_age_seconds: int = 86400,
                 clock=None) -> None:
        if not isinstance(verifiers, Mapping):
            raise TypeError("verifiers must be an explicit provider mapping")
        if type(max_receipt_age_seconds) is not int or not 1 <= max_receipt_age_seconds <= 31_536_000:
            raise ValueError("max_receipt_age_seconds must be between 1 and 31536000")
        copied = dict(verifiers)
        for provider_id, verifier in copied.items():
            if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
                raise ValueError("provider ids must be normalized lowercase identifiers")
            if not callable(getattr(verifier, "verify", None)):
                raise TypeError("each provider verifier must implement verify(request)")
        self._verifiers = copied
        self._max_age = max_receipt_age_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _validate_request(request: ReceiptVerificationRequest) -> None:
        if not isinstance(request, ReceiptVerificationRequest):
            raise TypeError("request must be ReceiptVerificationRequest")
        for name in ("provider_id", "owner_id", "session_id", "pending_id",
                     "operation_id", "evidence_reference"):
            value = getattr(request, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 1024:
                raise ValueError(f"{name} must be non-empty bounded text")
        if not _PROVIDER_ID.fullmatch(request.provider_id):
            raise ValueError("provider_id is not normalized")
        if not isinstance(request.action_digest, str) or not _SHA256.fullmatch(request.action_digest):
            raise ValueError("action_digest must be a lowercase SHA-256 digest")

    def verify(self, request: ReceiptVerificationRequest) -> VerifiedProviderReceipt:
        self._validate_request(request)
        verifier = self._verifiers.get(request.provider_id)
        if verifier is None:
            raise ProviderReceiptVerificationError("no trusted verifier is registered for this provider")
        try:
            receipt = verifier.verify(request)
        except Exception as exc:
            raise ProviderReceiptVerificationError("provider receipt verification failed") from exc
        if not isinstance(receipt, VerifiedProviderReceipt):
            raise ProviderReceiptVerificationError("provider verifier returned an invalid receipt type")
        expected = (
            request.provider_id, request.owner_id, request.session_id,
            request.pending_id, request.operation_id, request.action_digest,
        )
        actual = (
            receipt.provider_id, receipt.owner_id, receipt.session_id,
            receipt.pending_id, receipt.operation_id, receipt.action_digest,
        )
        if actual != expected:
            raise ProviderReceiptVerificationError("provider receipt identity or action binding mismatch")
        if receipt.resolution not in {"completed", "not_executed"}:
            raise ProviderReceiptVerificationError("provider receipt has an unsupported resolution")
        if not isinstance(receipt.receipt_id, str) or not receipt.receipt_id.strip() or len(receipt.receipt_id) > 256:
            raise ProviderReceiptVerificationError("provider receipt id is invalid")
        if not isinstance(receipt.evidence_digest, str) or not _SHA256.fullmatch(receipt.evidence_digest):
            raise ProviderReceiptVerificationError("provider evidence digest is invalid")
        if not isinstance(receipt.authority, str) or receipt.authority not in {
            "authenticated_provider_api", "verified_provider_signature"
        }:
            raise ProviderReceiptVerificationError("provider authority was not established")
        if not isinstance(receipt.verified_at, str) or len(receipt.verified_at) > 64:
            raise ProviderReceiptVerificationError("provider verification timestamp is invalid")
        try:
            verified_at = datetime.fromisoformat(receipt.verified_at.replace("Z", "+00:00"))
            if verified_at.tzinfo is None:
                raise ValueError("timezone required")
            now = self._clock()
            if now.tzinfo is None:
                raise ValueError("clock must be timezone-aware")
            age = now - verified_at.astimezone(timezone.utc)
            if age < timedelta(seconds=-60) or age > timedelta(seconds=self._max_age):
                raise ValueError("receipt is outside the accepted age window")
        except Exception as exc:
            raise ProviderReceiptVerificationError("provider verification timestamp is invalid or stale") from exc
        return receipt
