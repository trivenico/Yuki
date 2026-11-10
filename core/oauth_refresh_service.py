"""Single-attempt OAuth refresh coordinator with vault compare-and-swap.

Refresh credentials remain host-side. This module never returns token material
and deliberately does not retry an ambiguous provider refresh request.
"""
from __future__ import annotations

import time
from typing import Callable

from core.oauth_token_response import validate_oauth_token_response


class OAuthRefreshError(RuntimeError):
    """Stable, non-sensitive refresh lifecycle failure."""


class OAuthTokenRefreshService:
    """Refresh one owner/provider/account grant and atomically rotate its vault row."""

    def __init__(
        self, *, credential_vault, transport, token_endpoint: str,
        provider_id: str, client_id: str, issuer: str, resource: str,
        clock: Callable[[], float] = time.time,
        refresh_skew_seconds: int = 120,
    ):
        if not callable(getattr(credential_vault, "get", None)) or not callable(
            getattr(credential_vault, "rotate", None)
        ):
            raise TypeError("an encrypted, rotation-capable credential vault is required")
        if not callable(getattr(transport, "request_form", None)):
            raise TypeError("a pinned OAuth form transport is required")
        for label, value in {
            "token_endpoint": token_endpoint, "provider_id": provider_id,
            "client_id": client_id, "issuer": issuer, "resource": resource,
        }.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError("trusted OAuth refresh configuration is incomplete")
        if not callable(clock):
            raise ValueError("clock must be callable")
        if type(refresh_skew_seconds) is not int or not 0 <= refresh_skew_seconds <= 3600:
            raise ValueError("refresh_skew_seconds must be between 0 and 3600")
        self._vault = credential_vault
        self._transport = transport
        self._token_endpoint = token_endpoint
        self._provider_id = provider_id
        self._client_id = client_id
        self._issuer = issuer
        self._resource = resource
        self._clock = clock
        self._skew = refresh_skew_seconds

    def refresh_if_needed(self, *, owner_id: str, account_id: str) -> dict:
        """Return metadata unchanged while fresh; otherwise perform one refresh."""
        try:
            current = self._vault.get(
                owner_id=owner_id,
                provider_id=self._provider_id,
                account_id=account_id,
            )
        except Exception:
            raise OAuthRefreshError("credential_lookup_failed") from None
        if current is None:
            raise OAuthRefreshError("credential_not_found")
        if current.expires_at > int(self._clock()) + self._skew:
            return {
                "provider_id": self._provider_id,
                "account_id": account_id,
                "expires_at": current.expires_at,
                "scopes": current.scopes,
                "credential_version": current.version,
                "refreshed": False,
            }
        if not current.refresh_token:
            raise OAuthRefreshError("refresh_credential_unavailable")

        try:
            # Never retry: a network timeout can occur after the provider has
            # rotated and invalidated the old refresh token.
            document = self._transport.request_form(
                self._token_endpoint,
                fields={
                    "grant_type": "refresh_token",
                    "refresh_token": current.refresh_token,
                    "client_id": self._client_id,
                },
            )
            grant = validate_oauth_token_response(
                document,
                requested_scopes=current.scopes,
                expected_issuer=self._issuer,
                expected_resource=self._resource,
                clock=self._clock,
            )
            version = self._vault.rotate(
                owner_id=owner_id,
                provider_id=self._provider_id,
                account_id=account_id,
                expected_version=current.version,
                expected_refresh_token=current.refresh_token,
                access_token=grant.access_token,
                # None means the vault retains the current refresh token.
                refresh_token=grant.refresh_token,
                expires_at=grant.expires_at,
                scopes=grant.granted_scopes,
            )
        except Exception:
            raise OAuthRefreshError("credential_refresh_failed") from None

        return {
            "provider_id": self._provider_id,
            "account_id": account_id,
            "expires_at": grant.expires_at,
            "scopes": grant.granted_scopes,
            "credential_version": version,
            "refreshed": True,
        }
