"""Dedicated short-lived identity verification for the independent audit anchor.

This profile is intentionally distinct from task/lease-bound agent credentials:
the anchor is a service endpoint, not a task worker. Keys and replay state are
resolved through the same host-controlled trust contracts as workload identity.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import time
from typing import Callable

from core.workload_identity import (
    WorkloadCredentialState,
    WorkloadPublicKeyResolver,
    WorkloadSigningKey,
)

_TOKEN_TYPE = "yuki-audit-anchor+jwt"
_ALGORITHM = "EdDSA"
_MAX_TOKEN_BYTES = 8192
_MAX_TTL_SECONDS = 300
_MAX_CLOCK_SKEW_SECONDS = 30
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_TEXT = re.compile(r"^[^\\x00\\r\\n]{1,256}$")
_PUBLISH_SCOPES = frozenset({"audit-anchor:read", "audit-anchor:publish"})
_ADMIN_SCOPE = "audit-anchor:publishers:admin"
_ALLOWED_SCOPES = _PUBLISH_SCOPES | {_ADMIN_SCOPE}


class AuditAnchorCredentialError(PermissionError):
    """Credential rejection with a stable, non-sensitive reason."""


def _decode(value: str, maximum: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > maximum * 2 or not _SEGMENT.fullmatch(value):
        raise AuditAnchorCredentialError("credential_malformed")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise AuditAnchorCredentialError("credential_malformed") from exc
    if not raw or len(raw) > maximum or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise AuditAnchorCredentialError("credential_malformed")
    return raw


def _object(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_member")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AuditAnchorCredentialError("credential_malformed") from exc
    if not isinstance(value, dict):
        raise AuditAnchorCredentialError("credential_malformed")
    return value


class AuditAnchorCredentialAuthenticator:
    """Verify one-use Ed25519 credentials for a single anchor audience.

    The resolver is the key-rotation boundary: it must return only currently
    trusted issuer/key pairs. Retiring a key makes new requests fail closed.
    Credential state supplies atomic replay prevention and revocation.
    """

    def __init__(
        self,
        *,
        key_resolver: WorkloadPublicKeyResolver,
        credential_state: WorkloadCredentialState,
        audience: str,
        clock: Callable[[], float] = time.time,
        clock_skew_seconds: int = _MAX_CLOCK_SKEW_SECONDS,
    ):
        if not isinstance(audience, str) or not _TEXT.fullmatch(audience) or audience != audience.strip():
            raise ValueError("audit_anchor_audience_invalid")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        if type(clock_skew_seconds) is not int or not 0 <= clock_skew_seconds <= _MAX_CLOCK_SKEW_SECONDS:
            raise ValueError("audit_anchor_clock_skew_invalid")
        for value, methods in (
            (key_resolver, ("resolve",)),
            (credential_state, ("is_revoked", "consume_once")),
        ):
            if any(not callable(getattr(value, method, None)) for method in methods):
                raise TypeError("audit_anchor_identity_dependency_invalid")
        self._resolver = key_resolver
        self._state = credential_state
        self._audience = audience
        self._clock = clock
        self._skew = clock_skew_seconds

    def verify(self, token: str, *, required_scope: str, now: int | None = None) -> str:
        if required_scope not in _ALLOWED_SCOPES:
            raise AuditAnchorCredentialError("scope_not_supported")
        if not isinstance(token, str) or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise AuditAnchorCredentialError("credential_malformed")
        parts = token.split(".")
        if len(parts) != 3:
            raise AuditAnchorCredentialError("credential_malformed")
        header = _object(_decode(parts[0], 1024))
        claims = _object(_decode(parts[1], 4096))
        signature = _decode(parts[2], 128)
        if set(header) != {"typ", "alg", "kid"} or header.get("typ") != _TOKEN_TYPE or header.get("alg") != _ALGORITHM:
            raise AuditAnchorCredentialError("credential_header_invalid")
        issuer, kid = claims.get("iss"), header.get("kid")
        if any(not isinstance(v, str) or not _TEXT.fullmatch(v) or v != v.strip() for v in (issuer, kid)):
            raise AuditAnchorCredentialError("credential_issuer_or_key_invalid")
        try:
            key = self._resolver.resolve(issuer, kid, _ALGORITHM)
        except Exception as exc:
            raise AuditAnchorCredentialError("credential_trust_unavailable") from exc
        if (
            not isinstance(key, WorkloadSigningKey) or key.issuer != issuer
            or key.key_id != kid or key.algorithm != _ALGORITHM or len(key.public_key) != 32
        ):
            raise AuditAnchorCredentialError("credential_key_untrusted")
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(
                signature, (parts[0] + "." + parts[1]).encode("ascii")
            )
        except ImportError as exc:
            raise AuditAnchorCredentialError("credential_verifier_unavailable") from exc
        except (ValueError, InvalidSignature) as exc:
            raise AuditAnchorCredentialError("credential_signature_invalid") from exc

        required = {"iss", "sub", "aud", "iat", "nbf", "exp", "jti", "scope"}
        if set(claims) != required:
            raise AuditAnchorCredentialError("credential_claims_invalid")
        for name in ("iss", "sub", "aud", "jti"):
            value = claims.get(name)
            if not isinstance(value, str) or not _TEXT.fullmatch(value) or value != value.strip():
                raise AuditAnchorCredentialError("credential_claims_invalid")
        scopes = claims.get("scope")
        if (
            not isinstance(scopes, list) or not 1 <= len(scopes) <= len(_ALLOWED_SCOPES)
            or any(not isinstance(s, str) or s not in _ALLOWED_SCOPES for s in scopes)
            or len(set(scopes)) != len(scopes)
        ):
            raise AuditAnchorCredentialError("credential_scope_invalid")
        issued, not_before, expires = (claims.get("iat"), claims.get("nbf"), claims.get("exp"))
        if any(type(v) is not int or v < 0 for v in (issued, not_before, expires)):
            raise AuditAnchorCredentialError("credential_time_invalid")
        if not issued <= not_before < expires or expires - issued > _MAX_TTL_SECONDS:
            raise AuditAnchorCredentialError("credential_time_invalid")
        current = int(self._clock()) if now is None else now
        if type(current) is not int or current < 0:
            raise AuditAnchorCredentialError("credential_clock_invalid")
        if issued > current + self._skew or not_before > current + self._skew or expires <= current - self._skew:
            raise AuditAnchorCredentialError("credential_expired_or_not_yet_valid")
        if claims["aud"] != self._audience:
            raise AuditAnchorCredentialError("credential_audience_mismatch")
        if required_scope not in scopes:
            raise AuditAnchorCredentialError("credential_scope_insufficient")
        try:
            if self._state.is_revoked(issuer, claims["jti"]):
                raise AuditAnchorCredentialError("credential_revoked")
            if not self._state.consume_once(issuer, claims["jti"], expires):
                raise AuditAnchorCredentialError("credential_replayed")
        except AuditAnchorCredentialError:
            raise
        except Exception as exc:
            raise AuditAnchorCredentialError("credential_state_unavailable") from exc
        return claims["sub"]

    def authenticate_scope(self, scope) -> str | None:
        """ASGI-compatible authenticator; accepts only one Authorization header."""
        values = [
            value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and key.lower() == b"authorization"
        ]
        if len(values) != 1:
            return None
        try:
            scheme, token = values[0].decode("ascii").split(" ", 1)
            if scheme.lower() != "bearer" or not token or " " in token:
                return None
            method = scope.get("method", "GET").upper()
            if method not in {"GET", "PUT"}:
                return None
            required = "audit-anchor:read" if method == "GET" else "audit-anchor:publish"
            return self.verify(token, required_scope=required)
        except (UnicodeDecodeError, ValueError, AuditAnchorCredentialError):
            return None
