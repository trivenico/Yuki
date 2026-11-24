"""Trusted issuance boundary for task- and lease-bound workload credentials.

Identity and lease authorities are injected trusted-host adapters. The issuer
never accepts a caller-supplied Principal as proof of workload identity and
never stores or exports signing private keys.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
import secrets
import time
from typing import Callable, Protocol

from core.authorization import Principal
from core.workload_identity import WorkloadSigner, WorkloadSigningKey

_TOKEN_TYPE = "yuki-workload+jwt"
_ALGORITHM = "EdDSA"
_MAX_TTL_SECONDS = 900
_MAX_SCOPES = 64
_TEXT = re.compile(r"^[^\x00\r\n]{1,256}$")
_SCOPE = re.compile(r"^[^\x00\r\n]{1,128}$")


class WorkloadCredentialIssuanceError(PermissionError):
    """Stable issuance failure that does not expose identity or key material."""


@dataclass(frozen=True)
class ActiveTaskLease:
    """Lease facts returned by the trusted task-lease authority."""

    task_id: str
    worker_id: str
    generation: str
    expires_at: int

    def __post_init__(self) -> None:
        for name in ("task_id", "worker_id", "generation"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _TEXT.fullmatch(value) or value != value.strip():
                raise ValueError("invalid_task_lease")
        if type(self.expires_at) is not int or self.expires_at < 0:
            raise ValueError("invalid_task_lease_expiry")


class WorkloadIdentitySource(Protocol):
    """Trusted bootstrap adapter that authenticates an opaque workload handle."""

    def authenticate(self, workload_handle: object) -> Principal:
        """Return a Principal only after authenticating the presented workload."""


class TaskLeaseAuthority(Protocol):
    """Trusted adapter to the authoritative task lease store."""

    def active_lease(self, task_id: str) -> ActiveTaskLease | None:
        """Return the current lease snapshot, or None if no active lease exists."""


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_segment(value: dict) -> str:
    raw = json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("ascii")
    return _b64url(raw)


class WorkloadCredentialIssuer:
    """Issue short-lived Ed25519 credentials after host-side identity and lease checks.

    The signer must be backed by a non-exportable host/KMS key. A Principal passed
    directly by an agent is never accepted: every issuance resolves the opaque
    workload handle through the injected identity authority.
    """

    def __init__(
        self,
        *,
        identity_source: WorkloadIdentitySource,
        lease_authority: TaskLeaseAuthority,
        signer: WorkloadSigner,
        clock: Callable[[], float] = time.time,
    ) -> None:
        for value, methods, label in (
            (identity_source, ("authenticate",), "identity_source"),
            (lease_authority, ("active_lease",), "lease_authority"),
            (signer, ("active_key", "sign"), "signer"),
        ):
            if any(not callable(getattr(value, method, None)) for method in methods):
                raise TypeError(label + " does not implement its required contract")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._identity_source = identity_source
        self._lease_authority = lease_authority
        self._signer = signer
        self._clock = clock

    def issue(
        self,
        *,
        workload_handle: object,
        task_id: str,
        audience: str,
        scopes: frozenset[str],
        ttl_seconds: int = 300,
        delegation_id: str | None = None,
    ) -> str:
        if (
            not isinstance(task_id, str) or not _TEXT.fullmatch(task_id)
            or task_id != task_id.strip()
            or not isinstance(audience, str) or not _TEXT.fullmatch(audience)
            or audience != audience.strip()
            or not isinstance(scopes, frozenset)
            or not 1 <= len(scopes) <= _MAX_SCOPES
            or any(not isinstance(scope, str) or not _SCOPE.fullmatch(scope)
                   or scope != scope.strip() for scope in scopes)
            or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS
            or (delegation_id is not None and (
                not isinstance(delegation_id, str) or not _TEXT.fullmatch(delegation_id)
                or delegation_id != delegation_id.strip()
            ))
        ):
            raise WorkloadCredentialIssuanceError("issuance_request_invalid")

        try:
            principal = self._identity_source.authenticate(workload_handle)
        except Exception as exc:
            raise WorkloadCredentialIssuanceError("workload_authentication_failed") from exc
        if (
            not isinstance(principal, Principal)
            or principal.kind != "workload"
            or not principal.tenant_id
            or not principal.agent_id
            or not scopes.issubset(principal.permissions)
        ):
            raise WorkloadCredentialIssuanceError("workload_identity_or_scope_rejected")

        try:
            lease = self._lease_authority.active_lease(task_id)
        except Exception as exc:
            raise WorkloadCredentialIssuanceError("task_lease_unavailable") from exc
        now = self._clock()
        if type(now) not in (int, float) or now < 0:
            raise WorkloadCredentialIssuanceError("issuer_clock_invalid")
        issued_at = int(now)
        if (
            not isinstance(lease, ActiveTaskLease)
            or lease.task_id != task_id
            or lease.worker_id != principal.principal_id
            or lease.expires_at <= issued_at
        ):
            raise WorkloadCredentialIssuanceError("active_task_lease_required")

        try:
            key = self._signer.active_key(self._configured_issuer())
        except WorkloadCredentialIssuanceError:
            raise
        except Exception as exc:
            raise WorkloadCredentialIssuanceError("workload_signing_key_unavailable") from exc
        if (
            not isinstance(key, WorkloadSigningKey)
            or key.algorithm != _ALGORITHM
            or len(key.public_key) != 32
        ):
            raise WorkloadCredentialIssuanceError("workload_signing_key_invalid")

        claims = {
            "iss": key.issuer,
            "sub": principal.principal_id,
            "tenant_id": principal.tenant_id,
            "agent_id": principal.agent_id,
            "aud": audience,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": min(issued_at + ttl_seconds, lease.expires_at),
            "jti": secrets.token_urlsafe(32),
            "task_id": task_id,
            "lease_generation": lease.generation,
            "scope": sorted(scopes),
        }
        if claims["exp"] <= issued_at:
            raise WorkloadCredentialIssuanceError("task_lease_too_short")
        if delegation_id is not None:
            claims["delegation_id"] = delegation_id
        header = {"typ": _TOKEN_TYPE, "alg": _ALGORITHM, "kid": key.key_id}
        signing_input = (_json_segment(header) + "." + _json_segment(claims)).encode("ascii")
        try:
            signature = self._signer.sign(key.issuer, key.key_id, signing_input)
        except Exception as exc:
            raise WorkloadCredentialIssuanceError("workload_signing_failed") from exc
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise WorkloadCredentialIssuanceError("workload_signature_invalid")
        return signing_input.decode("ascii") + "." + _b64url(signature)

    def _configured_issuer(self) -> str:
        """Require signer implementations to expose a configured issuer identifier."""
        issuer = getattr(self._signer, "issuer", None)
        if not isinstance(issuer, str) or not _TEXT.fullmatch(issuer) or issuer != issuer.strip():
            raise WorkloadCredentialIssuanceError("workload_issuer_not_configured")
        return issuer
