"""HMAC signing primitives for reviewer-result integrity.

Keys must be provisioned and protected by the host (for example, an OS
credential store or KMS). This module deliberately does not persist keys.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


class ReviewerSignatureError(ValueError):
    """Invalid signing key, key identifier, or signature envelope."""


def canonical_review_bytes(payload: dict) -> bytes:
    if not isinstance(payload, dict):
        raise ReviewerSignatureError("review_payload_must_be_object")
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReviewerSignatureError("review_payload_not_canonicalizable") from exc


@dataclass(frozen=True)
class ReviewerSignature:
    key_id: str
    algorithm: str
    signature_hex: str

    def validate(self) -> None:
        if not isinstance(self.key_id, str) or not _KEY_ID.fullmatch(self.key_id):
            raise ReviewerSignatureError("invalid_reviewer_signing_key_id")
        if self.algorithm != "hmac-sha256":
            raise ReviewerSignatureError("unsupported_reviewer_signature_algorithm")
        if not isinstance(self.signature_hex, str) or not re.fullmatch(r"[a-f0-9]{64}", self.signature_hex):
            raise ReviewerSignatureError("invalid_reviewer_signature")


class HMACReviewerSigner:
    """HMAC-SHA256 signer. The caller owns key storage, rotation, and access."""

    def __init__(self, *, key_id: str, key: bytes):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ReviewerSignatureError("reviewer_signing_key_must_be_at_least_32_bytes")
        self.key_id = key_id
        self._key = key
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            raise ReviewerSignatureError("invalid_reviewer_signing_key_id")

    def sign(self, payload: dict) -> ReviewerSignature:
        digest = hmac.new(self._key, canonical_review_bytes(payload), hashlib.sha256).hexdigest()
        return ReviewerSignature(self.key_id, "hmac-sha256", digest)

    def verify(self, payload: dict, signature: ReviewerSignature) -> bool:
        if not isinstance(signature, ReviewerSignature):
            return False
        try:
            signature.validate()
            if signature.key_id != self.key_id:
                return False
            expected = self.sign(payload).signature_hex
            return hmac.compare_digest(expected, signature.signature_hex)
        except (ReviewerSignatureError, TypeError, ValueError):
            return False


__all__ = ["HMACReviewerSigner", "ReviewerSignature", "ReviewerSignatureError",
           "canonical_review_bytes"]
