"""Trusted OAuth authorization-code exchange coordinator.

This service composes existing PKCE, durable transaction, pinned transport,
strict token-response validation, and encrypted credential-vault primitives.
It is a host-side building block; it does not expose an HTTP callback route.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from core.oauth2_pkce import (
    OAuthAuthorizationError,
    create_pkce_authorization_request,
    validate_oauth_authorization_callback,
)
from core.oauth_token_response import validate_oauth_token_response


class OAuthAuthorizationServiceError(RuntimeError):
    """Stable, non-sensitive authorization orchestration failure."""


class OAuthAuthorizationService:
    """Run a single provider authorization flow under a trusted host identity."""

    def __init__(
        self, *, transaction_store, credential_vault, transport,
        provider_id: str, account_id: str, client_id: str,
        authorization_endpoint: str, token_endpoint: str,
        allowed_authorization_hosts, redirect_uri: str,
        issuer: str, resource: str, scopes,
    ):
        required = {
            "provider_id": provider_id, "account_id": account_id,
            "client_id": client_id, "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint, "redirect_uri": redirect_uri,
            "issuer": issuer, "resource": resource,
        }
        if any(not isinstance(value, str) or not value.strip() for value in required.values()):
            raise ValueError("trusted OAuth provider configuration is incomplete")
        if not callable(getattr(transaction_store, "create", None)) or not callable(
            getattr(transaction_store, "consume", None)
        ):
            raise TypeError("a durable OAuth transaction store is required")
        if not callable(getattr(credential_vault, "store", None)):
            raise TypeError("an encrypted OAuth credential vault is required")
        if not callable(getattr(transport, "request_form", None)):
            raise TypeError("a pinned OAuth form transport is required")
        if not isinstance(scopes, (list, tuple)) or not scopes:
            raise ValueError("at least one OAuth scope is required")
        self._transactions = transaction_store
        self._vault = credential_vault
        self._transport = transport
        self._provider_id = provider_id
        self._account_id = account_id
        self._client_id = client_id
        self._authorization_endpoint = authorization_endpoint
        self._token_endpoint = token_endpoint
        self._allowed_authorization_hosts = tuple(allowed_authorization_hosts)
        self._redirect_uri = redirect_uri
        self._issuer = issuer
        self._resource = resource
        self._scopes = tuple(scopes)

    def begin(self, *, owner_id: str, session_id: str) -> dict:
        """Create an owner/session-bound, short-lived S256 authorization request."""
        request = create_pkce_authorization_request(
            authorization_endpoint=self._authorization_endpoint,
            allowed_authorization_hosts=self._allowed_authorization_hosts,
            client_id=self._client_id,
            redirect_uri=self._redirect_uri,
            scopes=self._scopes,
            resource=self._resource,
        )
        try:
            expires_at = self._transactions.create(
                state=request.state,
                code_verifier=request.code_verifier,
                owner_id=owner_id,
                session_id=session_id,
                redirect_uri=request.redirect_uri,
                issuer=self._issuer,
                resource=self._resource,
                ttl_seconds=600,
            )
        except Exception:
            raise OAuthAuthorizationServiceError("authorization_transaction_creation_failed") from None
        return {"authorization_url": request.authorization_url, "expires_at": expires_at}

    @staticmethod
    def _callback_state(callback_url: str) -> str:
        if not isinstance(callback_url, str) or len(callback_url) > 8192:
            raise OAuthAuthorizationServiceError("invalid_callback")
        try:
            fields = parse_qs(
                urlsplit(callback_url).query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=16,
            )
        except (ValueError, UnicodeError):
            raise OAuthAuthorizationServiceError("invalid_callback") from None
        values = fields.get("state", [])
        if len(values) != 1 or not values[0] or len(values[0]) > 256:
            raise OAuthAuthorizationServiceError("invalid_callback_state")
        return values[0]

    def complete(self, *, owner_id: str, session_id: str, callback_url: str) -> dict:
        """Validate, atomically consume, exchange once, and persist a token grant."""
        state = self._callback_state(callback_url)
        try:
            validate_oauth_authorization_callback(
                callback_url=callback_url,
                expected_redirect_uri=self._redirect_uri,
                expected_state=state,
            )
            transaction = self._transactions.consume(
                state=state, owner_id=owner_id, session_id=session_id,
            )
        except Exception:
            raise OAuthAuthorizationServiceError("authorization_callback_rejected") from None

        if (
            transaction.redirect_uri != self._redirect_uri
            or transaction.issuer != self._issuer
            or transaction.resource != self._resource
        ):
            raise OAuthAuthorizationServiceError("authorization_transaction_context_mismatch")

        try:
            callback = validate_oauth_authorization_callback(
                callback_url=callback_url,
                expected_redirect_uri=transaction.redirect_uri,
                expected_state=transaction.state,
            )
            # Authorization codes are single-use. Never retry an ambiguous exchange.
            document = self._transport.request_form(
                self._token_endpoint,
                fields={
                    "grant_type": "authorization_code",
                    "code": callback.code,
                    "redirect_uri": transaction.redirect_uri,
                    "code_verifier": transaction.code_verifier,
                    "client_id": self._client_id,
                },
            )
            grant = validate_oauth_token_response(
                document,
                requested_scopes=self._scopes,
                expected_issuer=transaction.issuer,
                expected_resource=transaction.resource,
            )
            version = self._vault.store(
                owner_id=owner_id,
                provider_id=self._provider_id,
                account_id=self._account_id,
                access_token=grant.access_token,
                refresh_token=grant.refresh_token,
                expires_at=grant.expires_at,
                scopes=grant.granted_scopes,
            )
        except Exception:
            raise OAuthAuthorizationServiceError("authorization_exchange_failed") from None

        return {
            "provider_id": self._provider_id,
            "account_id": self._account_id,
            "expires_at": grant.expires_at,
            "scopes": grant.granted_scopes,
            "credential_version": version,
        }
