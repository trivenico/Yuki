"""Trusted, bounded OAuth metadata discovery for remote MCP resources.

The discovery coordinator performs GET-only metadata retrieval through an
injected pinned transport. It never registers a client, starts authorization,
stores credentials, or enables tools.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from core.mcp_oauth_metadata import (
    AuthorizationServerMetadata,
    ClientRegistrationPlan,
    OAuthMetadataError,
    ProtectedResourceMetadata,
    authorization_server_metadata_urls,
    choose_client_registration,
    protected_resource_metadata_urls,
    validate_authorization_server_metadata,
    validate_protected_resource_metadata,
)


@dataclass(frozen=True)
class MCPOAuthDiscoveryResult:
    resource_metadata: ProtectedResourceMetadata
    authorization_server_metadata: AuthorizationServerMetadata
    registration: ClientRegistrationPlan
    resource_metadata_url: str
    authorization_server_metadata_url: str


class TrustedMCPOAuthDiscovery:
    """Discover OAuth metadata only from explicitly allowed HTTPS hosts."""

    def __init__(self, *, transport, allowed_metadata_hosts):
        if not callable(getattr(transport, "request_json", None)):
            raise TypeError("a trusted OAuth JSON transport is required")
        if not isinstance(allowed_metadata_hosts, (set, frozenset, tuple, list)) or not allowed_metadata_hosts:
            raise ValueError("an explicit metadata host allow-list is required")
        hosts = set()
        for host in allowed_metadata_hosts:
            if not isinstance(host, str) or not host or host != host.strip():
                raise ValueError("invalid metadata host allow-list")
            hosts.add(host.lower().rstrip("."))
        self._transport = transport
        self._allowed_hosts = frozenset(hosts)

    def _fetch_document(self, url):
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower().rstrip(".")
        except ValueError:
            raise OAuthMetadataError("invalid_metadata_url") from None
        if parts.scheme != "https" or host not in self._allowed_hosts:
            raise OAuthMetadataError("metadata_host_not_allowed")
        try:
            document = self._transport.request_json("GET", url)
        except Exception:
            raise OAuthMetadataError("metadata_endpoint_unavailable") from None
        if not isinstance(document, dict):
            raise OAuthMetadataError("invalid_metadata_document")
        return document

    def _fetch_first_available(self, urls):
        last_error = None
        for url in urls:
            try:
                return self._fetch_document(url), url
            except OAuthMetadataError as exc:
                # A host-policy or malformed-document failure is terminal. Only
                # transport unavailability permits trying the next standard URL.
                if str(exc) != "metadata_endpoint_unavailable":
                    raise
                last_error = exc
        raise last_error or OAuthMetadataError("metadata_endpoint_unavailable")

    def discover(
        self,
        *,
        resource: str,
        expected_issuer: str | None = None,
        preregistered_client_id: str | None = None,
    ) -> MCPOAuthDiscoveryResult:
        """Discover and validate the resource's single trusted auth server."""
        resource_urls = protected_resource_metadata_urls(resource)
        resource_document, resource_url = self._fetch_first_available(resource_urls)
        resource_metadata = validate_protected_resource_metadata(
            resource_document, expected_resource=resource,
        )

        issuers = resource_metadata.authorization_servers
        if expected_issuer is not None:
            if expected_issuer not in issuers:
                raise OAuthMetadataError("authorization_server_not_advertised")
            issuer = expected_issuer
        else:
            if len(issuers) != 1:
                raise OAuthMetadataError("authorization_server_selection_required")
            issuer = issuers[0]

        metadata_urls = authorization_server_metadata_urls(issuer)
        server_document, server_url = self._fetch_first_available(metadata_urls)
        server_metadata = validate_authorization_server_metadata(
            server_document, expected_issuer=issuer,
        )
        registration = choose_client_registration(
            server_metadata,
            preregistered_client_id=preregistered_client_id,
        )
        return MCPOAuthDiscoveryResult(
            resource_metadata=resource_metadata,
            authorization_server_metadata=server_metadata,
            registration=registration,
            resource_metadata_url=resource_url,
            authorization_server_metadata_url=server_url,
        )
