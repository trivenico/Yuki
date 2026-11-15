"""Trusted Google OAuth composition for Yuki Gmail integration.

The host supplies durable transaction storage, encrypted credentials, and one
pinned transport covering the exact Google authorization, token, JWKS, and
userinfo hosts. Identity is verified here; the host maps verified OIDC sub to
an opaque owner-scoped account ID.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from core.google_oauth_identity import (
    GOOGLE_OIDC_ISSUER, GOOGLE_OIDC_JWKS_URI, GOOGLE_OIDC_USERINFO_URI,
)
from core.oauth_authorization_service import OAuthAuthorizationService
from core.oauth_provider_identity import OAuthProviderIdentityVerifier
from core.oidc_id_token import OIDCIdentityTokenVerifier

GOOGLE_PROVIDER_ID = "google"
GOOGLE_ISSUER = GOOGLE_OIDC_ISSUER
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_RESOURCE = "https://gmail.googleapis.com/"
GOOGLE_HOSTS = (
    "accounts.google.com",
    "oauth2.googleapis.com",
    "www.googleapis.com",
    "openidconnect.googleapis.com",
)
GOOGLE_GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)


class GoogleOAuthConfigurationError(ValueError):
    """Stable Google OAuth configuration validation failure."""


def build_google_gmail_authorization_service(
    *, client_id: str, redirect_uri: str, transaction_store,
    credential_vault, pinned_transport, account_id_resolver,
    clock=None, jwks_cache=None,
) -> OAuthAuthorizationService:
    """Build Google authorization-code + PKCE with cryptographic OIDC binding."""
    if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 512:
        raise GoogleOAuthConfigurationError("invalid_google_client_id")
    if not isinstance(redirect_uri, str) or len(redirect_uri) > 2048:
        raise GoogleOAuthConfigurationError("invalid_google_redirect_uri")
    try:
        callback = urlsplit(redirect_uri)
        port = callback.port
    except ValueError:
        raise GoogleOAuthConfigurationError("invalid_google_redirect_uri") from None
    if (
        callback.scheme != "https" or not callback.hostname
        or callback.username is not None or callback.password is not None
        or port not in (None, 443) or callback.query or callback.fragment
        or callback.path != "/oauth/callback/google"
    ):
        raise GoogleOAuthConfigurationError("invalid_google_redirect_uri")
    if not set(GOOGLE_HOSTS).issubset(getattr(pinned_transport, "allowed_hosts", ())):
        raise GoogleOAuthConfigurationError("google_oauth_transport_host_not_allowed")
    if not callable(account_id_resolver):
        raise GoogleOAuthConfigurationError("verified_google_account_resolver_required")
    oidc_kwargs = dict(
        transport=pinned_transport, jwks_uri=GOOGLE_OIDC_JWKS_URI,
        issuer=GOOGLE_ISSUER, client_id=client_id, expected_subject=None,
    )
    if clock is not None:
        oidc_kwargs["clock"] = clock
    if jwks_cache is not None:
        oidc_kwargs["jwks_cache"] = jwks_cache
    oidc_verifier = OIDCIdentityTokenVerifier(**oidc_kwargs)
    identity_verifier = OAuthProviderIdentityVerifier(
        transport=pinned_transport, userinfo_endpoint=GOOGLE_OIDC_USERINFO_URI,
        expected_subject=None, expected_issuer=GOOGLE_ISSUER,
    )
    return OAuthAuthorizationService(
        transaction_store=transaction_store, credential_vault=credential_vault,
        transport=pinned_transport, provider_id=GOOGLE_PROVIDER_ID, account_id=None,
        client_id=client_id, authorization_endpoint=GOOGLE_AUTHORIZATION_ENDPOINT,
        token_endpoint=GOOGLE_TOKEN_ENDPOINT, allowed_authorization_hosts=GOOGLE_HOSTS,
        redirect_uri=redirect_uri, issuer=GOOGLE_ISSUER, resource=GOOGLE_RESOURCE,
        scopes=GOOGLE_GMAIL_SCOPES, identity_verifier=identity_verifier,
        oidc_verifier=oidc_verifier, account_id_resolver=account_id_resolver,
        include_resource_parameter=False,
        authorization_parameters=(
            ("access_type", "offline"), ("include_granted_scopes", "true"),
            ("prompt", "consent"),
        ),
    )
