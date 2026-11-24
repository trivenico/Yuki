"""Strict Ed25519 verifier for task- and lease-bound Yuki workload credentials.

Credentials use a deliberately small compact-JWS profile. This module never
loads keys from token-controlled URLs and never accepts algorithm negotiation.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from core.workload_identity import (
    WorkloadCredentialClaims,
    WorkloadCredentialState,
    WorkloadPublicKeyResolver,
    WorkloadSigningKey,
)

_TOKEN_TYPE = "yuki-workload+jwt"
_ALGORITHM = "EdDSA"
_MAX_TOKEN_BYTES = 16_384
_MAX_HEADER_BYTES = 2_048
_MAX_PAYLOAD_BYTES = 12_288
_MAX_CLOCK_SKEW_SECONDS = 30
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_REQUIRED_CLAIMS = {
    "iss", "sub", "tenant_id", "agent_id", "aud", "iat", "nbf",
    "exp", "jti", "task_id", "lease_generation", "scope",
}
_OPTIONAL_CLAIMS = {"delegation_id"}


class WorkloadCredentialError(PermissionError):
    """Credential rejected; messages intentionally avoid token or key material."""


def _decode_segment(segment: str, maximum: int) -> bytes:
    if not isinstance(segment, str) or not segment or len(segment) > maximum * 2:
        raise WorkloadCredentialError("credential_malformed")
    if not _SEGMENT_RE.fullmatch(segment):
        raise WorkloadCredentialError("credential_malformed")
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (ValueError, binascii.Error) as exc:
        raise WorkloadCredentialError("credential_malformed") from exc
    if not raw or len(raw) > maximum:
        raise WorkloadCredentialError("credential_malformed")
    # Reject non-canonical encodings.
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != segment:
        raise WorkloadCredentialError("credential_malformed")
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkloadCredentialError("credential_malformed") from exc
    if not isinstance(value, dict):
        raise WorkloadCredentialError("credential_malformed")
    return value


def _claim_text(value: object, maximum: int = 256) -> bool:
    return (
        isinstance(value, str) and bool(value) and len(value) <= maximum
        and value == value.strip() and "\x00" not in value
    )


class Ed25519WorkloadCredentialVerifier:
    """Verify one-use, short-lived workload credentials against pinned trust.

    The state store's consume_once operation makes each credential single-use.
    Callers must obtain a fresh credential for each protected request. State
    backend failures reject the credential; they never fall back to stateless
    acceptance.
    """

    def __init__(
        self,
        *,
        key_resolver: WorkloadPublicKeyResolver,
        credential_state: WorkloadCredentialState,
        clock_skew_seconds: int = _MAX_CLOCK_SKEW_SECONDS,
    ) -> None:
        if not isinstance(clock_skew_seconds, int) or isinstance(clock_skew_seconds, bool):
            raise ValueError("clock skew must be an integer")
        if not 0 <= clock_skew_seconds <= _MAX_CLOCK_SKEW_SECONDS:
            raise ValueError("clock skew exceeds the allowed maximum")
        self._key_resolver = key_resolver
        self._credential_state = credential_state
        self._clock_skew_seconds = clock_skew_seconds

    def verify(
        self,
        token: str,
        *,
        expected_audience: str,
        expected_task_id: str,
        expected_lease_generation: str,
        now: int,
    ) -> WorkloadCredentialClaims:
        if (
            not isinstance(token, str) or not token
            or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES
            or not _claim_text(expected_audience)
            or not _claim_text(expected_task_id)
            or not _claim_text(expected_lease_generation)
            or type(now) is not int or now < 0
        ):
            raise WorkloadCredentialError("credential_invalid_request")

        parts = token.split(".")
        if len(parts) != 3:
            raise WorkloadCredentialError("credential_malformed")
        header_raw = _decode_segment(parts[0], _MAX_HEADER_BYTES)
        payload_raw = _decode_segment(parts[1], _MAX_PAYLOAD_BYTES)
        signature = _decode_segment(parts[2], 128)
        header = _json_object(header_raw)
        untrusted = _json_object(payload_raw)

        if set(header) != {"typ", "alg", "kid"}:
            raise WorkloadCredentialError("credential_header_invalid")
        if header.get("typ") != _TOKEN_TYPE or header.get("alg") != _ALGORITHM:
            raise WorkloadCredentialError("credential_algorithm_rejected")
        kid = header.get("kid")
        issuer = untrusted.get("iss")
        if not _claim_text(kid) or not _claim_text(issuer, 512):
            raise WorkloadCredentialError("credential_issuer_or_key_invalid")

        # Issuer is untrusted until signature verification. It is used only as
        # an exact lookup key in the locally configured trust resolver.
        try:
            key = self._key_resolver.resolve(issuer, kid, _ALGORITHM)
        except Exception as exc:
            raise WorkloadCredentialError("credential_trust_unavailable") from exc
        if (
            not isinstance(key, WorkloadSigningKey)
            or key.issuer != issuer or key.key_id != kid
            or key.algorithm != _ALGORITHM
            or len(key.public_key) != 32
        ):
            raise WorkloadCredentialError("credential_key_untrusted")

        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            public_key = Ed25519PublicKey.from_public_bytes(key.public_key)
            signing_input = (parts[0] + "." + parts[1]).encode("ascii")
            public_key.verify(signature, signing_input)
        except ImportError as exc:
            raise WorkloadCredentialError("credential_verifier_unavailable") from exc
        except (ValueError, InvalidSignature) as exc:
            raise WorkloadCredentialError("credential_signature_invalid") from exc

        # Only after signature verification are payload fields trusted.
        if set(untrusted) - (_REQUIRED_CLAIMS | _OPTIONAL_CLAIMS):
            raise WorkloadCredentialError("credential_claims_invalid")
        if not _REQUIRED_CLAIMS.issubset(untrusted):
            raise WorkloadCredentialError("credential_claims_invalid")
        scope = untrusted.get("scope")
        if (
            not isinstance(scope, list) or not 1 <= len(scope) <= 64
            or any(not _claim_text(item, 128) for item in scope)
            or len(set(scope)) != len(scope)
        ):
            raise WorkloadCredentialError("credential_scope_invalid")
        for field, maximum in (
            ("iss", 512), ("sub", 256), ("tenant_id", 256), ("agent_id", 256),
            ("aud", 256), ("jti", 256), ("task_id", 256),
            ("lease_generation", 256),
        ):
            if not _claim_text(untrusted.get(field), maximum):
                raise WorkloadCredentialError("credential_claims_invalid")
        if "delegation_id" in untrusted and not _claim_text(untrusted["delegation_id"]):
            raise WorkloadCredentialError("credential_claims_invalid")

        issued, not_before, expires = (untrusted.get("iat"), untrusted.get("nbf"), untrusted.get("exp"))
        if any(type(value) is not int or value < 0 for value in (issued, not_before, expires)):
            raise WorkloadCredentialError("credential_time_invalid")
        if not issued <= not_before < expires or expires - issued > 900:
            raise WorkloadCredentialError("credential_time_invalid")
        skew = self._clock_skew_seconds
        if issued > now + skew or not_before > now + skew or expires <= now - skew:
            raise WorkloadCredentialError("credential_expired_or_not_yet_valid")
        if untrusted["aud"] != expected_audience:
            raise WorkloadCredentialError("credential_audience_mismatch")
        if untrusted["task_id"] != expected_task_id:
            raise WorkloadCredentialError("credential_task_mismatch")
        if untrusted["lease_generation"] != expected_lease_generation:
            raise WorkloadCredentialError("credential_lease_mismatch")

        try:
            if self._credential_state.is_revoked(issuer, untrusted["jti"]):
                raise WorkloadCredentialError("credential_revoked")
            if not self._credential_state.consume_once(issuer, untrusted["jti"], expires):
                raise WorkloadCredentialError("credential_replayed")
        except WorkloadCredentialError:
            raise
        except Exception as exc:
            raise WorkloadCredentialError("credential_state_unavailable") from exc

        try:
            return WorkloadCredentialClaims(
                issuer=issuer,
                subject=untrusted["sub"],
                tenant_id=untrusted["tenant_id"],
                agent_id=untrusted["agent_id"],
                audience=untrusted["aud"],
                issued_at=issued,
                not_before=not_before,
                expires_at=expires,
                token_id=untrusted["jti"],
                task_id=untrusted["task_id"],
                lease_generation=untrusted["lease_generation"],
                scopes=frozenset(scope),
                delegation_id=untrusted.get("delegation_id"),
            )
        except (TypeError, ValueError) as exc:
            raise WorkloadCredentialError("credential_claims_invalid") from exc
