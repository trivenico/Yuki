"""Authenticated workload identity through OAuth 2.0 token introspection.

The introspection endpoint, expected issuer, audience, client ID, and endpoint
authentication are trusted host configuration. Access tokens are opaque: this
module never decodes token-controlled JWT headers or claims. Each verification
performs a fresh RFC 7662 introspection so revocation is observed immediately.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable, Mapping

from core.authorization import Principal


class WorkloadIdentityError(RuntimeError):
    """Stable, non-sensitive workload credential verification failure."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")


@dataclass(frozen=True)
class VerifiedWorkloadIdentity:
    """Identity and token metadata returned only after trusted introspection."""

    principal: Principal
    issuer: str
    audience: str
    token_id: str
    issued_at: int
    expires_at: int


class OAuthWorkloadIdentityVerifier:
    """Resolve a bearer credential into a workload Principal using RFC 7662.

    The transport must be the repository's pinned, redirect-free HTTPS
    transport. Endpoint credentials are supplied by the host and are never
    derived from the untrusted workload token.
    """

    def __init__(
        self,
        *,
        transport,
        introspection_endpoint: str,
        issuer: str,
        audience: str,
        client_id: str,
        introspection_headers: Mapping[str, str],
        clock: Callable[[], float] = time.time,
        clock_skew_seconds: int = 30,
        maximum_lifetime_seconds: int = 900,
    ) -> None:
        if not callable(getattr(transport, "request_form", None)):
            raise TypeError("a pinned OAuth form transport is required")
        for label, value, maximum in (
            ("introspection_endpoint", introspection_endpoint, 2048),
            ("issuer", issuer, 2048),
            ("audience", audience, 512),
            ("client_id", client_id, 256),
        ):
            if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
                raise ValueError(label + " must be trusted, bounded configuration")
        if not isinstance(introspection_headers, Mapping) or not introspection_headers:
            raise ValueError("trusted introspection endpoint authentication is required")
        headers = dict(introspection_headers)
        if any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            or any(ord(c) < 0x20 or ord(c) == 0x7f for c in k + v)
            for k, v in headers.items()
        ):
            raise ValueError("invalid introspection authentication headers")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(clock_skew_seconds) is not int or not 0 <= clock_skew_seconds <= 120:
            raise ValueError("clock skew must be between 0 and 120 seconds")
        if type(maximum_lifetime_seconds) is not int or not 1 <= maximum_lifetime_seconds <= 3600:
            raise ValueError("maximum token lifetime must be between 1 and 3600 seconds")
        self._transport = transport
        self._endpoint = introspection_endpoint
        self._issuer = issuer
        self._audience = audience
        self._client_id = client_id
        self._headers = headers
        self._clock = clock
        self._skew = clock_skew_seconds
        self._max_lifetime = maximum_lifetime_seconds

    @staticmethod
    def _timestamp(claims: dict, name: str) -> int:
        value = claims.get(name)
        if type(value) is not int or value < 0:
            raise WorkloadIdentityError("workload_claim_" + name + "_invalid")
        return value

    @staticmethod
    def _text(claims: dict, name: str, maximum: int = 256) -> str:
        value = claims.get(name)
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or len(value) > maximum:
            raise WorkloadIdentityError("workload_claim_" + name + "_invalid")
        return value

    def verify(
        self,
        *,
        access_token: str,
        expected_task_id: str | None = None,
    ) -> VerifiedWorkloadIdentity:
        if not isinstance(access_token, str) or not 1 <= len(access_token) <= 8192:
            raise WorkloadIdentityError("workload_credential_invalid")
        if any(ord(char) < 0x21 or ord(char) > 0x7e for char in access_token):
            raise WorkloadIdentityError("workload_credential_invalid")
        if expected_task_id is not None and (
            not isinstance(expected_task_id, str)
            or not _IDENTIFIER.fullmatch(expected_task_id)
        ):
            raise ValueError("expected_task_id must be a bounded identifier")

        try:
            claims = self._transport.request_form(
                self._endpoint,
                fields={"token": access_token, "token_type_hint": "access_token"},
                headers=self._headers,
            )
        except Exception:
            raise WorkloadIdentityError("workload_introspection_unavailable") from None
        if not isinstance(claims, dict):
            raise WorkloadIdentityError("workload_introspection_invalid")
        if claims.get("active") is not True:
            raise WorkloadIdentityError("workload_credential_inactive")
        if claims.get("iss") != self._issuer:
            raise WorkloadIdentityError("workload_issuer_mismatch")
        if claims.get("client_id") != self._client_id:
            raise WorkloadIdentityError("workload_client_mismatch")

        subject = self._text(claims, "sub")
        tenant_id = self._text(claims, "tenant_id")
        agent_id = self._text(claims, "agent_id", 80)
        token_id = self._text(claims, "jti")
        audiences = claims.get("aud")
        if isinstance(audiences, str):
            audiences = [audiences]
        if (
            not isinstance(audiences, list) or not audiences
            or any(not isinstance(item, str) or not item for item in audiences)
            or len(set(audiences)) != len(audiences)
            or self._audience not in audiences
        ):
            raise WorkloadIdentityError("workload_audience_mismatch")
        if claims.get("token_type", "Bearer") != "Bearer":
            raise WorkloadIdentityError("workload_token_type_invalid")

        issued_at = self._timestamp(claims, "iat")
        expires_at = self._timestamp(claims, "exp")
        now = int(self._clock())
        if (
            expires_at <= now - self._skew
            or issued_at > now + self._skew
            or expires_at <= issued_at
            or expires_at - issued_at > self._max_lifetime
        ):
            raise WorkloadIdentityError("workload_token_time_invalid")

        raw_scopes = claims.get("scope")
        if not isinstance(raw_scopes, str) or not raw_scopes or len(raw_scopes) > 4096:
            raise WorkloadIdentityError("workload_scopes_invalid")
        scopes = raw_scopes.split(" ")
        if (
            not 1 <= len(scopes) <= 64
            or any(not _SCOPE.fullmatch(scope) for scope in scopes)
            or len(set(scopes)) != len(scopes)
        ):
            raise WorkloadIdentityError("workload_scopes_invalid")

        bound_task = claims.get("task_id")
        if expected_task_id is not None and bound_task != expected_task_id:
            raise WorkloadIdentityError("workload_task_binding_mismatch")
        if bound_task is not None and (
            not isinstance(bound_task, str) or not _IDENTIFIER.fullmatch(bound_task)
        ):
            raise WorkloadIdentityError("workload_task_binding_invalid")

        principal = Principal(
            principal_id=subject,
            kind="workload",
            tenant_id=tenant_id,
            agent_id=agent_id,
            permissions=frozenset(scopes),
        )
        return VerifiedWorkloadIdentity(
            principal=principal,
            issuer=self._issuer,
            audience=self._audience,
            token_id=token_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
