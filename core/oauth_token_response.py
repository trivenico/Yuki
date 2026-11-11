"""Strict OAuth token-response parsing without credential persistence.

A parsed grant is still untrusted until its transport, issuer, client, and resource
context have been validated by the caller. Opaque access tokens are never decoded
or treated as JWTs by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Callable


class OAuthTokenResponseError(ValueError):
    """Stable, non-sensitive token response validation failure."""


_TOKEN = re.compile(r"^[A-Za-z0-9._~+/-]{1,8192}={0,}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")
_ALLOWED_FIELDS = frozenset({
    "access_token", "token_type", "expires_in", "refresh_token", "scope", "id_token",
})


@dataclass(frozen=True)
class OAuthTokenGrant:
    access_token: str = field(repr=False)
    token_type: str
    expires_at: int
    granted_scopes: tuple[str, ...]
    refresh_token: str | None = field(default=None, repr=False)
    id_token: str | None = field(default=None, repr=False)
    issuer: str = ""
    resource: str = ""


def _opaque_token(value, *, label: str, required: bool):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise OAuthTokenResponseError("invalid_" + label)
    return value


def _scopes(value, *, label):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise OAuthTokenResponseError("invalid_" + label)
    values = value.split(" ")
    if any(not _SCOPE.fullmatch(item) for item in values):
        raise OAuthTokenResponseError("invalid_" + label)
    if len(set(values)) != len(values):
        raise OAuthTokenResponseError("duplicate_" + label)
    return tuple(values)


def validate_oauth_token_response(
    document,
    *,
    requested_scopes,
    expected_issuer: str,
    expected_resource: str,
    clock: Callable[[], float] = time.time,
    maximum_lifetime_seconds: int = 31_536_000,
) -> OAuthTokenGrant:
    """Validate token response shape and ensure granted scopes do not widen."""
    if not isinstance(document, dict):
        raise OAuthTokenResponseError("invalid_token_response")
    if set(document) - _ALLOWED_FIELDS:
        raise OAuthTokenResponseError("unsupported_token_response_fields")
    if not isinstance(expected_issuer, str) or not expected_issuer or len(expected_issuer) > 2048:
        raise OAuthTokenResponseError("invalid_expected_issuer")
    if not isinstance(expected_resource, str) or not expected_resource or len(expected_resource) > 2048:
        raise OAuthTokenResponseError("invalid_expected_resource")
    if not isinstance(requested_scopes, (list, tuple)) or not requested_scopes or len(requested_scopes) > 64:
        raise OAuthTokenResponseError("invalid_requested_scopes")
    requested = []
    for item in requested_scopes:
        if not isinstance(item, str) or not _SCOPE.fullmatch(item) or item in requested:
            raise OAuthTokenResponseError("invalid_requested_scopes")
        requested.append(item)

    access_token = _opaque_token(document.get("access_token"), label="access_token", required=True)
    token_type = document.get("token_type")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise OAuthTokenResponseError("unsupported_token_type")
    if type(maximum_lifetime_seconds) is not int or not 1 <= maximum_lifetime_seconds <= 31_536_000:
        raise OAuthTokenResponseError("invalid_maximum_token_lifetime")
    expires_in = document.get("expires_in")
    if type(expires_in) is not int or not 1 <= expires_in <= maximum_lifetime_seconds:
        raise OAuthTokenResponseError("invalid_token_lifetime")

    refresh_token = _opaque_token(document.get("refresh_token"), label="refresh_token", required=False)
    id_token = document.get("id_token")
    if id_token is not None and (
        not isinstance(id_token, str) or not 1 <= len(id_token) <= 32768
        or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", id_token)
    ):
        raise OAuthTokenResponseError("invalid_id_token")
    raw_scope = document.get("scope")
    granted = tuple(requested) if raw_scope is None else _scopes(raw_scope, label="granted_scopes")
    if not set(granted).issubset(set(requested)):
        raise OAuthTokenResponseError("granted_scope_exceeds_request")

    now = int(clock())
    if now < 0 or now > 9_223_372_036_854_775_807 - expires_in:
        raise OAuthTokenResponseError("invalid_token_expiry")
    return OAuthTokenGrant(
        access_token=access_token,
        token_type="Bearer",
        expires_at=now + expires_in,
        granted_scopes=granted,
        refresh_token=refresh_token,
        id_token=id_token,
        issuer=expected_issuer,
        resource=expected_resource,
    )
