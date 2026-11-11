"""Owner-scoped OAuth-backed MCP client construction.

This is the host/runtime boundary between the OAuth credential lifecycle and
the restricted MCP HTTP client. Tokens are supplied through a private callback
and are never returned in connection metadata or tool results.
"""
from __future__ import annotations

import re
from typing import Callable

from core.mcp_client import MCPHTTPClient


class OAuthMCPConnectionError(RuntimeError):
    """Stable connection setup failure without credential disclosure."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")


class OAuthMCPConnectionManager:
    """Build restricted MCP clients backed by one trusted OAuth provider."""

    def __init__(
        self, *, provider_id: str, credential_vault, refresh_service,
        client_factory: Callable = MCPHTTPClient,
    ):
        if not isinstance(provider_id, str) or not _IDENTIFIER.fullmatch(provider_id):
            raise ValueError("provider_id is invalid")
        if not callable(getattr(credential_vault, "get", None)):
            raise TypeError("an encrypted OAuth credential vault is required")
        if not callable(getattr(refresh_service, "refresh_if_needed", None)):
            raise TypeError("an OAuth refresh service is required")
        if not callable(client_factory):
            raise TypeError("an MCP client factory is required")
        self._provider_id = provider_id
        self._vault = credential_vault
        self._refresh = refresh_service
        self._client_factory = client_factory

    @staticmethod
    def _scopes(values) -> frozenset[str]:
        if not isinstance(values, (tuple, list)) or len(values) > 64:
            raise ValueError("required_scopes must be a bounded list")
        if any(not isinstance(value, str) or not _SCOPE.fullmatch(value) for value in values):
            raise ValueError("required_scopes contains an invalid scope")
        if len(set(values)) != len(values):
            raise ValueError("required_scopes contains duplicate scopes")
        return frozenset(values)

    def create_client(
        self, *, owner_id: str, account_id: str, endpoint: str,
        allowed_hosts, allowed_tools, approver,
        required_scopes=(), timeout: float = 10,
    ):
        """Create a client whose private token callback refreshes just-in-time.

        owner_id must come from the authenticated host session, never model input.
        The endpoint, host/tool allow-lists and approval callback are trusted host
        configuration. The caller remains responsible for authorizing each
        owner-visible operation before exposing the client to an agent.
        """
        if not isinstance(owner_id, str) or not _IDENTIFIER.fullmatch(owner_id):
            raise OAuthMCPConnectionError("authenticated_owner_invalid")
        if not isinstance(account_id, str) or not _IDENTIFIER.fullmatch(account_id):
            raise OAuthMCPConnectionError("provider_account_invalid")
        scopes = self._scopes(required_scopes)
        if not callable(approver):
            raise OAuthMCPConnectionError("explicit_tool_approver_required")
        if not isinstance(allowed_tools, (set, frozenset, tuple, list)):
            raise OAuthMCPConnectionError("explicit_tool_allowlist_required")
        # An empty allow-list is an explicit deny-all policy. It permits
        # initialize/tools-list discovery while call_tool rejects every tool.
        if any(not isinstance(name, str) or not name or len(name) > 128 for name in allowed_tools):
            raise OAuthMCPConnectionError("invalid_tool_allowlist")

        def access_token_provider() -> str:
            # A refresh timeout is ambiguous; the refresh service deliberately
            # performs no retry. Credentials remain inside this closure.
            try:
                status = self._refresh.refresh_if_needed(
                    owner_id=owner_id, account_id=account_id,
                )
                if (not isinstance(status, dict)
                        or status.get("provider_id") != self._provider_id
                        or status.get("account_id") != account_id):
                    raise OAuthMCPConnectionError("credential_context_mismatch")
                granted = status.get("scopes")
                if not isinstance(granted, (tuple, list)) or not scopes.issubset(set(granted)):
                    raise OAuthMCPConnectionError("required_oauth_scope_missing")
                credential = self._vault.get(
                    owner_id=owner_id,
                    provider_id=self._provider_id,
                    account_id=account_id,
                )
                if (credential is None or credential.owner_id != owner_id
                        or credential.provider_id != self._provider_id
                        or credential.account_id != account_id
                        or not scopes.issubset(set(credential.scopes))):
                    raise OAuthMCPConnectionError("credential_not_available")
                token = credential.access_token
                if not isinstance(token, str) or not token:
                    raise OAuthMCPConnectionError("credential_not_available")
                return token
            except OAuthMCPConnectionError:
                raise
            except Exception:
                raise OAuthMCPConnectionError("credential_not_available") from None

        try:
            return self._client_factory(
                endpoint,
                allowed_hosts=allowed_hosts,
                allowed_tools=allowed_tools,
                approver=approver,
                access_token_provider=access_token_provider,
                timeout=timeout,
            )
        except OAuthMCPConnectionError:
            raise
        except Exception:
            raise OAuthMCPConnectionError("mcp_client_configuration_invalid") from None
