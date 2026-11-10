"""Strict MCP OAuth metadata validation and discovery planning.

This module is intentionally pure: it constructs candidate metadata URLs and
validates documents supplied by a trusted HTTP layer. It performs no network
requests, redirects, DNS resolution, registration, or credential persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class OAuthMetadataError(ValueError):
    """Stable, non-secret metadata validation failure."""


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    resource: str
    authorization_servers: tuple[str, ...]
    scopes_supported: tuple[str, ...]


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    revocation_endpoint: str | None
    code_challenge_methods_supported: tuple[str, ...]
    client_id_metadata_document_supported: bool
    authorization_response_iss_parameter_supported: bool


@dataclass(frozen=True)
class ClientRegistrationPlan:
    method: str
    endpoint: str | None = None


def _https_uri(value, *, label: str, allow_query: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise OAuthMetadataError(f"invalid_{label}")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise OAuthMetadataError(f"invalid_{label}") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or (not allow_query and parsed.query)
    ):
        raise OAuthMetadataError(f"invalid_{label}")
    return value


def _string_list(value, *, label: str, required: bool = False, maximum: int = 64):
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value) or len(value) > maximum:
        raise OAuthMetadataError(f"invalid_{label}")
    result = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 256
            or any(ord(char) < 0x21 for char in item)
        ):
            raise OAuthMetadataError(f"invalid_{label}")
        if item not in result:
            result.append(item)
    return tuple(result)


def protected_resource_metadata_urls(resource_url: str) -> tuple[str, ...]:
    """Return MCP path-specific then root RFC 9728 discovery locations."""
    resource = _https_uri(resource_url, label="resource")
    parsed = urlsplit(resource)
    path = parsed.path.rstrip("/")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    candidates = []
    if path and path != "/":
        candidates.append(origin + "/.well-known/oauth-protected-resource" + path)
    candidates.append(origin + "/.well-known/oauth-protected-resource")
    return tuple(dict.fromkeys(candidates))


def authorization_server_metadata_urls(issuer: str) -> tuple[str, str]:
    """Return RFC 8414 and OIDC discovery URLs for the exact issuer."""
    issuer = _https_uri(issuer, label="issuer")
    parsed = urlsplit(issuer)
    path = parsed.path.rstrip("/")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    rfc8414 = origin + "/.well-known/oauth-authorization-server" + path
    oidc = issuer.rstrip("/") + "/.well-known/openid-configuration"
    return rfc8414, oidc


def validate_protected_resource_metadata(
    document,
    *,
    expected_resource: str,
) -> ProtectedResourceMetadata:
    expected = _https_uri(expected_resource, label="resource")
    if not isinstance(document, dict):
        raise OAuthMetadataError("invalid_protected_resource_metadata")
    resource = _https_uri(document.get("resource"), label="metadata_resource")
    if resource != expected:
        raise OAuthMetadataError("protected_resource_mismatch")
    servers = _string_list(
        document.get("authorization_servers"),
        label="authorization_servers",
        required=True,
        maximum=10,
    )
    validated_servers = tuple(
        _https_uri(server, label="authorization_server_issuer")
        for server in servers
    )
    scopes = _string_list(
        document.get("scopes_supported"),
        label="scopes_supported",
        maximum=128,
    )
    return ProtectedResourceMetadata(
        resource=resource,
        authorization_servers=validated_servers,
        scopes_supported=scopes,
    )


def validate_authorization_server_metadata(
    document,
    *,
    expected_issuer: str,
) -> AuthorizationServerMetadata:
    expected = _https_uri(expected_issuer, label="issuer")
    if not isinstance(document, dict):
        raise OAuthMetadataError("invalid_authorization_server_metadata")
    issuer = _https_uri(document.get("issuer"), label="metadata_issuer")
    if issuer != expected:
        raise OAuthMetadataError("authorization_server_issuer_mismatch")

    authorization_endpoint = _https_uri(
        document.get("authorization_endpoint"),
        label="authorization_endpoint",
    )
    token_endpoint = _https_uri(document.get("token_endpoint"), label="token_endpoint")
    registration = document.get("registration_endpoint")
    revocation = document.get("revocation_endpoint")
    if registration is not None:
        registration = _https_uri(registration, label="registration_endpoint")
    if revocation is not None:
        revocation = _https_uri(revocation, label="revocation_endpoint")

    challenges = _string_list(
        document.get("code_challenge_methods_supported"),
        label="code_challenge_methods_supported",
    )
    # OAuth 2.1 public-client authorization must not silently downgrade PKCE.
    if "S256" not in challenges:
        raise OAuthMetadataError("pkce_s256_not_supported")

    cimd = document.get("client_id_metadata_document_supported", False)
    iss_response = document.get("authorization_response_iss_parameter_supported", False)
    if not isinstance(cimd, bool) or not isinstance(iss_response, bool):
        raise OAuthMetadataError("invalid_authorization_server_capabilities")

    return AuthorizationServerMetadata(
        issuer=issuer,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        registration_endpoint=registration,
        revocation_endpoint=revocation,
        code_challenge_methods_supported=challenges,
        client_id_metadata_document_supported=cimd,
        authorization_response_iss_parameter_supported=iss_response,
    )


def choose_client_registration(
    metadata: AuthorizationServerMetadata,
    *,
    preregistered_client_id: str | None = None,
) -> ClientRegistrationPlan:
    """Apply MCP registration priority: pre-registration, CIMD, then DCR."""
    if preregistered_client_id is not None:
        if (
            not isinstance(preregistered_client_id, str)
            or not preregistered_client_id.strip()
            or len(preregistered_client_id) > 512
            or any(ord(char) < 0x21 for char in preregistered_client_id)
        ):
            raise OAuthMetadataError("invalid_preregistered_client_id")
        return ClientRegistrationPlan("pre_registered")
    if metadata.client_id_metadata_document_supported:
        return ClientRegistrationPlan("client_id_metadata_document")
    if metadata.registration_endpoint:
        return ClientRegistrationPlan("dynamic_client_registration", metadata.registration_endpoint)
    raise OAuthMetadataError("client_registration_unavailable")
