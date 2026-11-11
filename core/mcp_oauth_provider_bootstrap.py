"""Trusted composition of MCP OAuth discovery, registration and authorization."""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from core.mcp_oauth_client_registration import PublicOAuthClientRegistrar
from core.mcp_oauth_discovery import TrustedMCPOAuthDiscovery
from core.mcp_oauth_metadata import OAuthMetadataError
from core.oauth_authorization_service import OAuthAuthorizationService

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")


def _https(value, label):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError(f"{label} must be HTTPS")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError(f"{label} must be HTTPS") from None
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.fragment):
        raise ValueError(f"{label} must be HTTPS")
    return parsed.hostname.lower().rstrip(".")


def _hosts(values, label, required=True):
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ValueError(f"{label} must be a host collection")
    result = set()
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{label} contains an invalid host")
        host = value.lower().rstrip(".")
        if "/" in host or ":" in host or "@" in host:
            raise ValueError(f"{label} contains an invalid host")
        result.add(host)
    if required and not result:
        raise ValueError(f"{label} must not be empty")
    return tuple(sorted(result))


@dataclass(frozen=True)
class OAuthProviderBootstrapConfig:
    provider_id: str
    account_id: str
    resource_uri: str
    redirect_uri: str
    scopes: tuple[str, ...]
    application_type: str
    allowed_metadata_hosts: tuple[str, ...]
    allowed_registration_hosts: tuple[str, ...]
    allowed_authorization_hosts: tuple[str, ...]
    expected_issuer: str | None = None
    preregistered_client_id: str | None = None
    cimd_document_uri: str | None = None
    client_name: str = "Yuki"

    def __post_init__(self):
        for label, value in (("provider_id", self.provider_id), ("account_id", self.account_id)):
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError(f"{label} is invalid")
        if self.application_type not in {"web", "native"}:
            raise ValueError("application_type must be web or native")
        resource_host = _https(self.resource_uri, "resource_uri")
        redirect_host = _https(self.redirect_uri, "redirect_uri")
        redirect = urlsplit(self.redirect_uri)
        if redirect.query or redirect.path != f"/oauth/callback/{self.provider_id}":
            raise ValueError("redirect_uri must use the provider-specific callback path")
        metadata_hosts = _hosts(self.allowed_metadata_hosts, "allowed_metadata_hosts")
        registration_hosts = _hosts(
            self.allowed_registration_hosts, "allowed_registration_hosts", required=False
        )
        authorization_hosts = _hosts(self.allowed_authorization_hosts, "allowed_authorization_hosts")
        if resource_host not in metadata_hosts:
            raise ValueError("resource host must be allow-listed for metadata")
        if self.expected_issuer is not None:
            if _https(self.expected_issuer, "expected_issuer") not in metadata_hosts:
                raise ValueError("issuer host must be allow-listed for metadata")
        if not isinstance(self.scopes, (tuple, list)) or not self.scopes or len(self.scopes) > 64:
            raise ValueError("scopes must be a non-empty bounded sequence")
        if any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in self.scopes):
            raise ValueError("scopes contain an invalid value")
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("scopes must be unique")
        if self.preregistered_client_id is not None and (
            not isinstance(self.preregistered_client_id, str)
            or not self.preregistered_client_id.strip()
            or len(self.preregistered_client_id) > 512
            or any(ord(c) < 0x21 or ord(c) == 0x7f for c in self.preregistered_client_id)
        ):
            raise ValueError("preregistered_client_id is invalid")
        if self.cimd_document_uri is not None:
            _https(self.cimd_document_uri, "cimd_document_uri")
        if not isinstance(self.client_name, str) or not self.client_name.strip() or len(self.client_name) > 100:
            raise ValueError("client_name is invalid")
        object.__setattr__(self, "allowed_metadata_hosts", metadata_hosts)
        object.__setattr__(self, "allowed_registration_hosts", registration_hosts)
        object.__setattr__(self, "allowed_authorization_hosts", authorization_hosts)
        object.__setattr__(self, "scopes", tuple(self.scopes))


@dataclass(frozen=True)
class PreparedOAuthProvider:
    provider_id: str
    issuer: str
    resource: str
    client_id: str
    registration_method: str
    redirect_uri: str
    scopes: tuple[str, ...]
    authorization_service: OAuthAuthorizationService


class MCPProviderOAuthBootstrap:
    """Build the existing authorization service using host-owned provider config."""

    def __init__(
        self, *, metadata_transport, registration_transport, oauth_transport,
        transaction_store, credential_vault, identity_verifier_for_provider,
        oidc_verifier_for_provider=None,
    ):
        for transport, method, label in (
            (metadata_transport, "request_json", "metadata"),
            (registration_transport, "request_json", "registration"),
            (oauth_transport, "request_form", "authorization"),
        ):
            if not callable(getattr(transport, method, None)):
                raise TypeError(f"pinned OAuth {label} transport is required")
        if not callable(getattr(transaction_store, "create", None)) or not callable(
            getattr(transaction_store, "consume", None)
        ):
            raise TypeError("durable OAuth transaction store is required")
        if not callable(getattr(credential_vault, "store", None)):
            raise TypeError("encrypted OAuth credential vault is required")
        if not callable(identity_verifier_for_provider):
            raise TypeError("trusted identity verifier resolver is required")
        if oidc_verifier_for_provider is not None and not callable(oidc_verifier_for_provider):
            raise TypeError("OIDC verifier resolver must be callable")
        self._metadata_transport = metadata_transport
        self._registration_transport = registration_transport
        self._oauth_transport = oauth_transport
        self._transactions = transaction_store
        self._vault = credential_vault
        self._identity_for = identity_verifier_for_provider
        self._oidc_for = oidc_verifier_for_provider

    def prepare(self, config: OAuthProviderBootstrapConfig) -> PreparedOAuthProvider:
        if not isinstance(config, OAuthProviderBootstrapConfig):
            raise TypeError("trusted OAuth provider config is required")
        discovered = TrustedMCPOAuthDiscovery(
            transport=self._metadata_transport,
            allowed_metadata_hosts=config.allowed_metadata_hosts,
        ).discover(
            resource=config.resource_uri,
            expected_issuer=config.expected_issuer,
            preregistered_client_id=config.preregistered_client_id,
        )
        metadata = discovered.authorization_server_metadata
        auth_host = urlsplit(metadata.authorization_endpoint).hostname.lower().rstrip(".")
        token_host = urlsplit(metadata.token_endpoint).hostname.lower().rstrip(".")
        if auth_host not in config.allowed_authorization_hosts or token_host not in config.allowed_authorization_hosts:
            raise OAuthMetadataError("authorization_endpoint_host_not_allowed")
        if metadata.issuer not in discovered.resource_metadata.authorization_servers:
            raise OAuthMetadataError("authorization_server_not_advertised")
        registered = PublicOAuthClientRegistrar(
            transport=self._registration_transport,
            application_type=config.application_type,
            allowed_registration_hosts=config.allowed_registration_hosts,
        ).register(
            plan=discovered.registration,
            metadata=metadata,
            redirect_uri=config.redirect_uri,
            scopes=config.scopes,
            client_name=config.client_name,
            cimd_document_uri=config.cimd_document_uri,
            preregistered_client_id=config.preregistered_client_id,
        )
        identity = self._identity_for(config.provider_id)
        oidc = self._oidc_for(config.provider_id) if self._oidc_for else None
        service = OAuthAuthorizationService(
            transaction_store=self._transactions,
            credential_vault=self._vault,
            transport=self._oauth_transport,
            provider_id=config.provider_id,
            account_id=config.account_id,
            client_id=registered.client_id,
            authorization_endpoint=metadata.authorization_endpoint,
            token_endpoint=metadata.token_endpoint,
            allowed_authorization_hosts=config.allowed_authorization_hosts,
            redirect_uri=config.redirect_uri,
            issuer=metadata.issuer,
            resource=config.resource_uri,
            scopes=config.scopes,
            identity_verifier=identity,
            authorization_response_iss_parameter_supported=(
                metadata.authorization_response_iss_parameter_supported
            ),
            oidc_verifier=oidc,
        )
        return PreparedOAuthProvider(
            provider_id=config.provider_id,
            issuer=metadata.issuer,
            resource=config.resource_uri,
            client_id=registered.client_id,
            registration_method=registered.method,
            redirect_uri=config.redirect_uri,
            scopes=config.scopes,
            authorization_service=service,
        )
