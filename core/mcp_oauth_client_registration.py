"""Explicit public OAuth client registration for remote MCP providers.

This module never chooses an untrusted endpoint. It accepts only validated
authorization-server metadata and performs DCR only when the host policy
explicitly permits it. CIMD and pre-registered clients require no network write.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from core.mcp_oauth_metadata import (
    AuthorizationServerMetadata,
    ClientRegistrationPlan,
    OAuthMetadataError,
)


@dataclass(frozen=True)
class OAuthClientRegistration:
    method: str
    client_id: str
    redirect_uris: tuple[str, ...]
    scopes: tuple[str, ...]


def _https_url(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise OAuthMetadataError("invalid_" + label)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise OAuthMetadataError("invalid_" + label) from None
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or port not in (None, 443) or parsed.fragment
    ):
        raise OAuthMetadataError("invalid_" + label)
    return value


class PublicOAuthClientRegistrar:
    """Resolve a registration plan and, only for DCR, make one bounded POST."""

    def __init__(self, *, transport, allowed_registration_hosts=()):
        if not callable(getattr(transport, "request_json", None)):
            raise TypeError("a pinned OAuth JSON transport is required")
        if not isinstance(allowed_registration_hosts, (set, frozenset, tuple, list)):
            raise TypeError("registration host allow-list must be a collection")
        hosts = set()
        for host in allowed_registration_hosts:
            if not isinstance(host, str) or not host or host != host.strip():
                raise ValueError("invalid registration host allow-list")
            hosts.add(host.lower().rstrip("."))
        self._transport = transport
        self._allowed_hosts = frozenset(hosts)

    def register(
        self,
        *,
        plan: ClientRegistrationPlan,
        metadata: AuthorizationServerMetadata,
        redirect_uri: str,
        scopes,
        client_name: str = "Yuki",
        cimd_document_uri: str | None = None,
        preregistered_client_id: str | None = None,
    ) -> OAuthClientRegistration:
        redirect = _https_url(redirect_uri, "redirect_uri")
        if not isinstance(scopes, (tuple, list)) or not scopes or len(scopes) > 64:
            raise OAuthMetadataError("invalid_registration_scopes")
        normalized_scopes = []
        for scope in scopes:
            if (
                not isinstance(scope, str) or not scope or len(scope) > 256
                or any(ord(char) < 0x21 for char in scope)
            ):
                raise OAuthMetadataError("invalid_registration_scopes")
            if scope not in normalized_scopes:
                normalized_scopes.append(scope)
        if (
            not isinstance(client_name, str) or not client_name.strip()
            or len(client_name) > 100
            or any(ord(char) < 0x20 for char in client_name)
        ):
            raise OAuthMetadataError("invalid_client_name")

        if plan.method == "pre_registered":
            if (
                not isinstance(preregistered_client_id, str)
                or not preregistered_client_id.strip()
                or len(preregistered_client_id) > 512
                or any(ord(char) < 0x21 for char in preregistered_client_id)
            ):
                raise OAuthMetadataError("preregistered_client_id_required")
            return OAuthClientRegistration(
                "pre_registered", preregistered_client_id, (redirect,),
                tuple(normalized_scopes),
            )

        if plan.method == "client_id_metadata_document":
            if not metadata.client_id_metadata_document_supported:
                raise OAuthMetadataError("cimd_not_supported")
            document_uri = _https_url(cimd_document_uri, "cimd_document_uri")
            return OAuthClientRegistration(
                "client_id_metadata_document", document_uri, (redirect,),
                tuple(normalized_scopes),
            )

        if plan.method != "dynamic_client_registration":
            raise OAuthMetadataError("unsupported_registration_method")
        endpoint = metadata.registration_endpoint
        if not endpoint or plan.endpoint != endpoint:
            raise OAuthMetadataError("registration_endpoint_mismatch")
        try:
            host = (urlsplit(endpoint).hostname or "").lower().rstrip(".")
        except ValueError:
            raise OAuthMetadataError("invalid_registration_endpoint") from None
        if host not in self._allowed_hosts:
            raise OAuthMetadataError("registration_host_not_allowed")

        payload = {
            "client_name": client_name.strip(),
            "application_type": "web",
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": " ".join(normalized_scopes),
        }
        try:
            response = self._transport.request_json("POST", endpoint, payload=payload)
        except Exception:
            raise OAuthMetadataError("client_registration_failed") from None
        if not isinstance(response, dict):
            raise OAuthMetadataError("invalid_client_registration_response")
        client_id = response.get("client_id")
        if (
            not isinstance(client_id, str) or not client_id.strip()
            or len(client_id) > 512
            or any(ord(char) < 0x21 for char in client_id)
        ):
            raise OAuthMetadataError("invalid_client_registration_response")
        # Yuki is a public PKCE client. Never retain or silently accept a
        # client secret returned contrary to the requested authentication mode.
        if response.get("token_endpoint_auth_method", "none") != "none":
            raise OAuthMetadataError("client_authentication_method_mismatch")
        if response.get("client_secret") not in (None, ""):
            raise OAuthMetadataError("unexpected_public_client_secret")
        returned_redirects = response.get("redirect_uris")
        if returned_redirects is not None and returned_redirects != [redirect]:
            raise OAuthMetadataError("registered_redirect_uri_mismatch")
        return OAuthClientRegistration(
            "dynamic_client_registration", client_id, (redirect,),
            tuple(normalized_scopes),
        )
