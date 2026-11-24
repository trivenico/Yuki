"""Typed contracts for cryptographically authenticated workload identity.

This module deliberately does not issue or verify credentials. A Principal may
only be constructed by trusted verifier code after signature and claim checks.
The interfaces here separate signing-key custody, public-key resolution,
revocation, and replay protection from token parsing and policy evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_MAX_ID = 256
_MAX_ISSUER = 512
_MAX_AUDIENCE = 256
_MAX_SCOPES = 64
_MAX_SCOPE = 128
_MAX_TTL_SECONDS = 900
_ALLOWED_ALGORITHMS = frozenset({"EdDSA"})


def _text(value: object, field: str, maximum: int = _MAX_ID) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or "\x00" in value
    ):
        raise ValueError(f"{field} must be non-empty bounded trimmed text")
    return value


def _timestamp(value: object, field: str) -> int:
    # bool is an int subclass; reject it explicitly.
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer timestamp")
    return value


@dataclass(frozen=True)
class WorkloadCredentialClaims:
    """Validated, immutable claims expected after decoding a signed credential.

    Constructing this object does not authenticate the claims. Only a trusted
    verifier may use a successfully verified instance to create a Principal.
    """

    issuer: str
    subject: str
    tenant_id: str
    agent_id: str
    audience: str
    issued_at: int
    not_before: int
    expires_at: int
    token_id: str
    task_id: str
    lease_generation: str
    scopes: frozenset[str]
    delegation_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.issuer, "issuer", _MAX_ISSUER)
        _text(self.subject, "subject")
        _text(self.tenant_id, "tenant_id")
        _text(self.agent_id, "agent_id")
        _text(self.audience, "audience", _MAX_AUDIENCE)
        _text(self.token_id, "token_id")
        _text(self.task_id, "task_id")
        _text(self.lease_generation, "lease_generation")
        if self.delegation_id is not None:
            _text(self.delegation_id, "delegation_id")
        issued = _timestamp(self.issued_at, "issued_at")
        not_before = _timestamp(self.not_before, "not_before")
        expires = _timestamp(self.expires_at, "expires_at")
        if not issued <= not_before < expires:
            raise ValueError("credential validity timestamps are inconsistent")
        if expires - issued > _MAX_TTL_SECONDS:
            raise ValueError("credential lifetime exceeds the maximum")
        if not isinstance(self.scopes, frozenset) or not 1 <= len(self.scopes) <= _MAX_SCOPES:
            raise ValueError("scopes must be a non-empty bounded frozenset")
        for scope in self.scopes:
            _text(scope, "scope", _MAX_SCOPE)


@dataclass(frozen=True)
class WorkloadSigningKey:
    """Non-secret key metadata; private key material never appears in this model."""

    issuer: str
    key_id: str
    algorithm: str
    public_key: bytes

    def __post_init__(self) -> None:
        _text(self.issuer, "issuer", _MAX_ISSUER)
        _text(self.key_id, "key_id")
        if self.algorithm not in _ALLOWED_ALGORITHMS:
            raise ValueError("workload signing algorithm is not allowed")
        if not isinstance(self.public_key, bytes) or not 1 <= len(self.public_key) <= 4096:
            raise ValueError("public_key must be bounded bytes")


@runtime_checkable
class WorkloadSigner(Protocol):
    """Host/KMS-backed signer; implementations must not export private keys."""

    def active_key(self, issuer: str) -> WorkloadSigningKey:
        """Return public metadata for the active signing key."""
        ...

    def sign(self, issuer: str, key_id: str, message: bytes) -> bytes:
        """Sign bounded message bytes using a non-exportable private key."""
        ...


@runtime_checkable
class WorkloadPublicKeyResolver(Protocol):
    """Resolve keys only from a host-configured issuer trust registry."""

    def resolve(self, issuer: str, key_id: str, algorithm: str) -> WorkloadSigningKey | None:
        """Return trusted public metadata, or None for an unknown/retired key."""
        ...


@runtime_checkable
class WorkloadCredentialState(Protocol):
    """Atomic credential revocation and replay state boundary."""

    def is_revoked(self, issuer: str, token_id: str) -> bool:
        """Return whether this issuer/token identifier has been revoked."""
        ...

    def consume_once(self, issuer: str, token_id: str, expires_at: int) -> bool:
        """Atomically consume a one-time credential; False means replay or failure."""
        ...


@runtime_checkable
class WorkloadCredentialVerifier(Protocol):
    """Verification boundary; implementations must fail closed on every error."""

    def verify(
        self,
        token: str,
        *,
        expected_audience: str,
        expected_task_id: str,
        expected_lease_generation: str,
        now: int,
    ) -> WorkloadCredentialClaims:
        """Return claims only after signature, trust, time, binding and revocation checks."""
        ...


def principal_from_verified_claims(claims: WorkloadCredentialClaims):
    """Create a workload Principal from verifier output.

    This function is intentionally a narrow conversion helper, not a verifier.
    Callers must only pass claims returned by the configured trusted verifier.
    """
    from core.authorization import Principal

    if not isinstance(claims, WorkloadCredentialClaims):
        raise TypeError("claims must be WorkloadCredentialClaims")
    return Principal(
        principal_id=claims.subject,
        kind="workload",
        tenant_id=claims.tenant_id,
        agent_id=claims.agent_id,
        permissions=claims.scopes,
    )
