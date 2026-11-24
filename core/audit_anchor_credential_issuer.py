"""Short-lived credential issuer for enrolled audit-anchor publishers."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import math
import re
import secrets
import time
from typing import Callable, Protocol

from core.workload_identity import WorkloadSigner, WorkloadSigningKey

_TOKEN_TYPE = "yuki-audit-anchor+jwt"
_ALGORITHM = "EdDSA"
_MAX_TTL_SECONDS = 300
_TEXT = re.compile(r"^[!-~]{1,256}$")
_ALLOWED_SCOPES = frozenset({"audit-anchor:read", "audit-anchor:publish"})


class AuditAnchorIssuanceError(PermissionError):
    """Stable audit-anchor issuance failure."""


class AuditAnchorPublisherIdentitySource(Protocol):
    def authenticate(self, bootstrap_handle: object) -> str:
        """Authenticate opaque workload bootstrap material and return its identity."""


class AuditAnchorPublisherRegistry(Protocol):
    def is_publisher_active(self, publisher_identity: str) -> bool:
        """Return current publisher enrollment state."""


def _segment(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
                     separators=(",", ":"), sort_keys=True).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class AuditAnchorCredentialIssuer:
    """Issue anchor credentials only to authenticated, currently enrolled publishers.

    Signer private keys remain in the injected host/KMS implementation. Each
    issuance gets a fresh jti and a maximum five-minute lifetime.
    """

    identity_source: AuditAnchorPublisherIdentitySource
    publisher_registry: AuditAnchorPublisherRegistry
    signer: WorkloadSigner
    audience: str
    clock: Callable[[], float] = time.time

    def __post_init__(self):
        for value, methods in (
            (self.identity_source, ("authenticate",)),
            (self.publisher_registry, ("is_publisher_active",)),
            (self.signer, ("active_key", "sign")),
        ):
            if any(not callable(getattr(value, name, None)) for name in methods):
                raise TypeError("audit_anchor_issuer_dependency_invalid")
        if not isinstance(self.audience, str) or not _TEXT.fullmatch(self.audience) or self.audience != self.audience.strip():
            raise ValueError("audit_anchor_audience_invalid")
        if not callable(self.clock):
            raise TypeError("clock_must_be_callable")

    def issue(self, *, bootstrap_handle: object, scopes: frozenset[str],
              ttl_seconds: int = 120) -> str:
        if (
            not isinstance(scopes, frozenset) or not scopes
            or not scopes.issubset(_ALLOWED_SCOPES)
            or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS
        ):
            raise AuditAnchorIssuanceError("issuance_request_invalid")
        try:
            identity = self.identity_source.authenticate(bootstrap_handle)
        except Exception as exc:
            raise AuditAnchorIssuanceError("publisher_authentication_failed") from exc
        if not isinstance(identity, str) or not _TEXT.fullmatch(identity) or identity != identity.strip():
            raise AuditAnchorIssuanceError("publisher_identity_invalid")
        try:
            active = self.publisher_registry.is_publisher_active(identity)
        except Exception as exc:
            raise AuditAnchorIssuanceError("publisher_registry_unavailable") from exc
        if active is not True:
            raise AuditAnchorIssuanceError("publisher_not_enrolled")
        try:
            current = self.clock()
        except Exception as exc:
            raise AuditAnchorIssuanceError("issuer_clock_invalid") from exc
        if type(current) not in (int, float) or not math.isfinite(current) or current < 0:
            raise AuditAnchorIssuanceError("issuer_clock_invalid")
        issued = int(current)
        configured_issuer = getattr(self.signer, "issuer", None)
        if not isinstance(configured_issuer, str) or not _TEXT.fullmatch(configured_issuer):
            raise AuditAnchorIssuanceError("issuer_not_configured")
        try:
            key = self.signer.active_key(configured_issuer)
        except Exception as exc:
            raise AuditAnchorIssuanceError("signing_key_unavailable") from exc
        if (
            not isinstance(key, WorkloadSigningKey) or key.issuer != configured_issuer
            or key.algorithm != _ALGORITHM or len(key.public_key) != 32
        ):
            raise AuditAnchorIssuanceError("signing_key_invalid")
        claims = {
            "iss": configured_issuer, "sub": identity, "aud": self.audience,
            "iat": issued, "nbf": issued, "exp": issued + ttl_seconds,
            "jti": secrets.token_urlsafe(32), "scope": sorted(scopes),
        }
        signing_input = (_segment({"typ": _TOKEN_TYPE, "alg": _ALGORITHM, "kid": key.key_id})
                        + "." + _segment(claims)).encode("ascii")
        try:
            signature = self.signer.sign(configured_issuer, key.key_id, signing_input)
        except Exception as exc:
            raise AuditAnchorIssuanceError("signing_failed") from exc
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise AuditAnchorIssuanceError("signature_invalid")
        return signing_input.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
