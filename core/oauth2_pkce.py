"""OAuth 2.1 authorization-code + PKCE primitives.

This module creates authorization requests and validates callbacks. It deliberately
does not persist transactions, exchange codes, store tokens, refresh credentials,
or expose an HTTP callback server; those responsibilities belong to a trusted
host and encrypted credential broker.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")
class OAuthAuthorizationError(ValueError):
    """Stable, non-secret OAuth authorization validation failure."""


@dataclass(frozen=True)
class PKCEAuthorizationRequest:
    authorization_url: str
    state: str
    code_verifier: str
    code_challenge: str
    redirect_uri: str


@dataclass(frozen=True)
class OAuthAuthorizationCallback:
    code: str
    state: str
    issuer: str | None = None


def _https_url(value: str, *, label: str, allowed_hosts=None) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise OAuthAuthorizationError(f"invalid_{label}")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise OAuthAuthorizationError(f"invalid_{label}") from None
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or parts.fragment
    ):
        raise OAuthAuthorizationError(f"invalid_{label}")
    if allowed_hosts is not None:
        allowed = frozenset(
            item.lower().rstrip(".") for item in allowed_hosts
            if isinstance(item, str) and item
        )
        if host not in allowed:
            raise OAuthAuthorizationError(f"untrusted_{label}_host")
    return value


def create_pkce_authorization_request(
    *,
    authorization_endpoint: str,
    allowed_authorization_hosts,
    client_id: str,
    redirect_uri: str,
    scopes,
    resource: str,
    oidc_nonce: str | None = None,
) -> PKCEAuthorizationRequest:
    """Build a bounded S256 authorization request for a reviewed OAuth server."""
    endpoint = _https_url(
        authorization_endpoint,
        label="authorization_endpoint",
        allowed_hosts=allowed_authorization_hosts,
    )
    endpoint_parts = urlsplit(endpoint)
    if endpoint_parts.query:
        # Do not allow pre-seeded parameters to override or duplicate OAuth fields.
        raise OAuthAuthorizationError("authorization_endpoint_query_not_allowed")
    if (
        not isinstance(client_id, str)
        or not client_id.strip()
        or len(client_id) > 512
        or any(ord(char) < 0x21 for char in client_id)
    ):
        raise OAuthAuthorizationError("invalid_client_id")

    callback = _https_url(redirect_uri, label="redirect_uri")
    callback_parts = urlsplit(callback)
    if callback_parts.query:
        raise OAuthAuthorizationError("redirect_uri_query_not_allowed")

    resource_uri = _https_url(resource, label="resource")
    if not isinstance(scopes, (list, tuple)) or not 1 <= len(scopes) <= 32:
        raise OAuthAuthorizationError("invalid_scopes")
    normalized_scopes = []
    for scope in scopes:
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
            raise OAuthAuthorizationError("invalid_scopes")
        if scope not in normalized_scopes:
            normalized_scopes.append(scope)

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    if oidc_nonce is not None and (
        not isinstance(oidc_nonce, str) or not 16 <= len(oidc_nonce) <= 512
        or any(ord(char) < 0x21 for char in oidc_nonce)
    ):
        raise OAuthAuthorizationError("invalid_oidc_nonce")

    params = [
        ("response_type", "code"),
        ("client_id", client_id),
        ("redirect_uri", callback),
        ("scope", " ".join(normalized_scopes)),
        ("state", state),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        ("resource", resource_uri),
    ]
    if oidc_nonce is not None:
        params.append(("nonce", oidc_nonce))
    authorization_url = urlunsplit((
        endpoint_parts.scheme,
        endpoint_parts.netloc,
        endpoint_parts.path,
        urlencode(params),
        "",
    ))
    return PKCEAuthorizationRequest(
        authorization_url=authorization_url,
        state=state,
        code_verifier=verifier,
        code_challenge=challenge,
        redirect_uri=callback,
    )


def validate_oauth_authorization_callback(
    *,
    callback_url: str,
    expected_redirect_uri: str,
    expected_state: str,
    expected_issuer: str | None = None,
    require_issuer: bool = False,
) -> OAuthAuthorizationCallback:
    """Validate exact callback destination and one-time state before code exchange.

    The caller must consume its server-side transaction atomically after validation;
    this pure helper cannot prevent replay by itself.
    """
    expected = _https_url(expected_redirect_uri, label="redirect_uri")
    if type(require_issuer) is not bool:
        raise OAuthAuthorizationError("invalid_issuer_requirement")
    if expected_issuer is not None:
        expected_issuer = _https_url(expected_issuer, label="issuer")
    if require_issuer and expected_issuer is None:
        raise OAuthAuthorizationError("expected_issuer_required")
    if (
        not isinstance(expected_state, str)
        or not 32 <= len(expected_state) <= 256
    ):
        raise OAuthAuthorizationError("invalid_expected_state")
    if not isinstance(callback_url, str) or len(callback_url) > 8192:
        raise OAuthAuthorizationError("invalid_callback")

    try:
        actual_parts = urlsplit(callback_url)
        expected_parts = urlsplit(expected)
        fields = parse_qs(
            actual_parts.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except (ValueError, UnicodeError):
        raise OAuthAuthorizationError("invalid_callback") from None

    if (
        actual_parts.scheme != expected_parts.scheme
        or actual_parts.netloc != expected_parts.netloc
        or actual_parts.path != expected_parts.path
        or actual_parts.fragment
    ):
        raise OAuthAuthorizationError("callback_uri_mismatch")
    if any(len(values) != 1 for values in fields.values()):
        raise OAuthAuthorizationError("duplicate_callback_parameter")
    states = fields.get("state", [])
    if len(states) != 1 or not hmac.compare_digest(states[0], expected_state):
        raise OAuthAuthorizationError("callback_state_mismatch")
    if "error" in fields:
        raise OAuthAuthorizationError("authorization_was_denied")
    issuer_values = fields.get("iss", [])
    if require_issuer and len(issuer_values) != 1:
        raise OAuthAuthorizationError("authorization_response_issuer_missing")
    if issuer_values:
        if expected_issuer is None or not hmac.compare_digest(issuer_values[0].encode("utf-8"), expected_issuer.encode("utf-8")):
            raise OAuthAuthorizationError("authorization_response_issuer_mismatch")
    codes = fields.get("code", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
        raise OAuthAuthorizationError("invalid_authorization_code")
    if any(key not in {"code", "state", "iss"} for key in fields):
        raise OAuthAuthorizationError("unexpected_callback_parameter")
    return OAuthAuthorizationCallback(
        code=codes[0], state=states[0],
        issuer=issuer_values[0] if issuer_values else None,
    )
