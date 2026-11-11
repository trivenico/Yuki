"""Trusted composition root for remote MCP OAuth provider authorization.

All endpoints, redirect URIs, scopes, client identity and account binding are
operator-owned configuration. Discovery is explicit and GET-only; registration
follows the validated metadata plan. No provider is enabled by constructing a
service, and no model/user input can select a URL or registration host.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from core.mcp_oauth_client_registration import PublicOAuthClientRegistrar
from core.mcp_oauth_discovery import TrustedMCPOAuthDiscovery
from core.oauth_authorization_service import OAuthAuthorizationService


class MCPOAuthProviderConfigurationError(ValueError):
    """Stable failure for incomplete or unsafe trusted provider configuration."""


@dataclass(frozen=True)
class MCPOAuthProviderConfig:
    provider_id: str
    account_id: str
    resource: str
    redirect_uri: str
    scopes: tuple[str, ...]
    application_type: str
    metadata_hosts: tuple[str, ...]
    registration_hosts: tuple[str, ...]
    authorization_hosts: tuple[str, ...]
    redirect_hosts: tuple[str, ...]
    expected_issuer: str | None = None
    preregistered_client_id: str | None = None
    cimd_document_uri: str | None = None
    client_name: str = "Yuki"


def _trusted_identifier(value: str, label: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 256
            or value != value.strip()
            or any(ord(char) < 0x21 or ord(char) == 0x7f for char in value)):
        raise MCPOAuthProviderConfigurationError("invalid_" + label)
    return value


def _trusted_https_uri(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise MCPOAuthProviderConfigurationError("invalid_" + label)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise MCPOAuthProviderConfigurationError("invalid_" + label) from None
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.fragment):
        raise MCPOAuthProviderConfigurationError("invalid_" + label)
    return value


def _trusted_hosts(values, label: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not values:
        raise MCPOAuthProviderConfigurationError(label + "_required")
    hosts = []
    for value in values:
        if (not isinstance(value, str) or not value or value != value.strip()
                or "/" in value or ":" in value or any(ord(c) <= 0x20 for c in value)):
            raise MCPOAuthProviderConfigurationError("invalid_" + label)
        host = value.lower().rstrip(".")
        if host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def build_mcp_oauth_authorization_service(
    config: MCPOAuthProviderConfig,
    *,
    transport,
    transaction_store,
    credential_vault,
    identity_verifier,
    oidc_verifier=None,
) -> OAuthAuthorizationService:
    """Discover and register one configured provider, then return its auth service.

    Call only from trusted host startup/operator setup. Metadata retrieval and a
    possible DCR POST happen once per explicit construction; this function does
    not connect an account, exchange a token, or enable MCP tools.
    """
    if not isinstance(config, MCPOAuthProviderConfig):
        raise TypeError("trusted MCP OAuth provider config is required")
    provider_id = _trusted_identifier(config.provider_id, "provider_id")
    account_id = _trusted_identifier(config.account_id, "account_id")
    resource = _trusted_https_uri(config.resource, "resource")
    redirect_uri = _trusted_https_uri(config.redirect_uri, "redirect_uri")
    if config.application_type not in {"web", "native"}:
        raise MCPOAuthProviderConfigurationError("invalid_application_type")
    metadata_hosts = _trusted_hosts(config.metadata_hosts, "metadata_hosts")
    registration_hosts = _trusted_hosts(config.registration_hosts, "registration_hosts")
    authorization_hosts = _trusted_hosts(config.authorization_hosts, "authorization_hosts")
    redirect_hosts = _trusted_hosts(config.redirect_hosts, "redirect_hosts")
    redirect_host = (urlsplit(redirect_uri).hostname or "").lower().rstrip(".")
    if redirect_host not in redirect_hosts:
        raise MCPOAuthProviderConfigurationError("redirect_host_not_allowed")
    if not isinstance(config.scopes, (tuple, list)) or not config.scopes or len(config.scopes) > 64:
        raise MCPOAuthProviderConfigurationError("invalid_scopes")
    scopes = tuple(dict.fromkeys(
        _trusted_identifier(scope, "scope") for scope in config.scopes
    ))
    if config.expected_issuer is not None:
        expected_issuer = _trusted_https_uri(config.expected_issuer, "issuer")
    else:
        expected_issuer = None
    if config.preregistered_client_id is not None:
        _trusted_identifier(config.preregistered_client_id, "preregistered_client_id")
    if config.cimd_document_uri is not None:
        _trusted_https_uri(config.cimd_document_uri, "cimd_document_uri")

    discovery = TrustedMCPOAuthDiscovery(
        transport=transport, allowed_metadata_hosts=metadata_hosts,
    )
    result = discovery.discover(
        resource=resource,
        expected_issuer=expected_issuer,
        preregistered_client_id=config.preregistered_client_id,
    )
    registrar = PublicOAuthClientRegistrar(
        transport=transport,
        application_type=config.application_type,
        allowed_registration_hosts=registration_hosts,
    )
    client = registrar.register(
        plan=result.registration,
        metadata=result.authorization_server_metadata,
        redirect_uri=redirect_uri,
        scopes=scopes,
        client_name=config.client_name,
        cimd_document_uri=config.cimd_document_uri,
        preregistered_client_id=config.preregistered_client_id,
    )
    return OAuthAuthorizationService(
        transaction_store=transaction_store,
        credential_vault=credential_vault,
        transport=transport,
        provider_id=provider_id,
        account_id=account_id,
        client_id=client.client_id,
        authorization_endpoint=result.authorization_server_metadata.authorization_endpoint,
        token_endpoint=result.authorization_server_metadata.token_endpoint,
        allowed_authorization_hosts=authorization_hosts,
        redirect_uri=redirect_uri,
        issuer=result.authorization_server_metadata.issuer,
        resource=resource,
        scopes=scopes,
        identity_verifier=identity_verifier,
        authorization_response_iss_parameter_supported=(
            result.authorization_server_metadata.authorization_response_iss_parameter_supported
        ),
        oidc_verifier=oidc_verifier,
    )
