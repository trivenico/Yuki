"""Owner- and account-bound access to already-authorized provider credentials.

This resolver is intentionally read-only: it never starts OAuth, refreshes a
grant, widens scopes, or falls back to another account. Callers must separately
use the provider's registered pinned HTTPS transport.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Callable


class OAuthProviderCredentialError(RuntimeError):
    """Stable, non-sensitive provider credential resolution failure."""


@dataclass(frozen=True)
class ProviderAccessCredential:
    owner_id: str
    provider_id: str
    account_id: str
    access_token: str = field(repr=False)
    expires_at: int
    scopes: tuple[str, ...]
    version: int


class OAuthProviderCredentialResolver:
    """Resolve one exact owner's registered account and verify its granted scopes."""

    def __init__(self, *, connection_registry, credential_vault,
                 clock: Callable[[], float] = time.time, expiry_skew_seconds: int = 60):
        if not callable(getattr(connection_registry, "get", None)):
            raise TypeError("an OAuth connection registry is required")
        if not callable(getattr(credential_vault, "get", None)):
            raise TypeError("an encrypted OAuth credential vault is required")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(expiry_skew_seconds) is not int or not 0 <= expiry_skew_seconds <= 3600:
            raise ValueError("expiry_skew_seconds must be between 0 and 3600")
        self._connections = connection_registry
        self._vault = credential_vault
        self._clock = clock
        self._skew = expiry_skew_seconds

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}", value
        ):
            raise OAuthProviderCredentialError("invalid_" + label)
        return value

    @staticmethod
    def _required_scopes(scopes) -> tuple[str, ...]:
        if not isinstance(scopes, (tuple, list)) or not scopes or len(scopes) > 64:
            raise OAuthProviderCredentialError("invalid_required_scopes")
        if any(not isinstance(scope, str) or not scope or len(scope) > 128
               or any(ord(ch) < 33 or ord(ch) > 126 for ch in scope)
               for scope in scopes):
            raise OAuthProviderCredentialError("invalid_required_scopes")
        if len(set(scopes)) != len(scopes):
            raise OAuthProviderCredentialError("duplicate_required_scopes")
        return tuple(sorted(scopes))

    def resolve(self, *, owner_id: str, provider_id: str, account_id: str,
                required_scopes) -> ProviderAccessCredential:
        """Return a short-lived access credential only for the exact connected account."""
        owner = self._identifier(owner_id, "owner_id")
        provider = self._identifier(provider_id, "provider_id")
        account = self._identifier(account_id, "account_id")
        required = self._required_scopes(required_scopes)
        try:
            connection = self._connections.get(
                owner_id=owner, provider_id=provider, account_id=account,
            )
            credential = self._vault.get(
                owner_id=owner, provider_id=provider, account_id=account,
            )
        except Exception:
            raise OAuthProviderCredentialError("provider_credential_lookup_failed") from None
        if connection is None or credential is None:
            raise OAuthProviderCredentialError("provider_account_not_connected")
        if (
            connection.provider_id != provider or connection.account_id != account
            or credential.owner_id != owner or credential.provider_id != provider
            or credential.account_id != account
        ):
            raise OAuthProviderCredentialError("provider_account_identity_mismatch")
        try:
            connection_scopes = set(connection.scopes)
            credential_scopes = set(credential.scopes)
        except (TypeError, AttributeError):
            raise OAuthProviderCredentialError("provider_scope_metadata_invalid") from None
        if connection_scopes != credential_scopes:
            raise OAuthProviderCredentialError("provider_scope_metadata_mismatch")
        if not set(required).issubset(credential_scopes):
            raise OAuthProviderCredentialError("provider_scope_not_granted")
        if (
            type(credential.expires_at) is not int
            or credential.expires_at <= int(self._clock()) + self._skew
            or type(credential.version) is not int or credential.version < 1
            or not isinstance(credential.access_token, str) or not credential.access_token
        ):
            raise OAuthProviderCredentialError("provider_access_credential_unavailable")
        return ProviderAccessCredential(
            owner_id=owner, provider_id=provider, account_id=account,
            access_token=credential.access_token, expires_at=credential.expires_at,
            scopes=tuple(sorted(credential_scopes)), version=credential.version,
        )
