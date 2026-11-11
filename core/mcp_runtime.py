"""Host-composed, owner-scoped MCP runtime service.

This is the application boundary that combines trusted provider connection
configuration, durable policy snapshots, immediate revision fencing, and
metadata-only policy/execution audit. It never discovers or trusts provider
tool metadata as authorization and never creates provider configurations.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from core.approval_audit import MCPExecutionAudit, MCPToolPolicyAudit, SQLiteApprovalAuditLog
from core.mcp_tool_policy_store import SQLiteMCPToolPolicyStore
from core.oauth_mcp_connection import OAuthMCPConnectionManager


@dataclass(frozen=True)
class MCPProviderConnectionConfig:
    """Trusted host configuration for one already-prepared provider."""

    endpoint: str
    allowed_hosts: tuple[str, ...]
    required_scopes: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.endpoint, str):
            raise ValueError("mcp_endpoint_invalid")
        try:
            parsed = urlsplit(self.endpoint)
            endpoint_host = parsed.hostname
        except ValueError:
            raise ValueError("mcp_endpoint_invalid") from None
        if (parsed.scheme != "https" or not endpoint_host or parsed.username
                or parsed.password or parsed.fragment):
            raise ValueError("mcp_endpoint_must_be_trusted_https")
        if not isinstance(self.allowed_hosts, tuple) or not self.allowed_hosts:
            raise ValueError("mcp_allowed_hosts_required")
        if any(not isinstance(host, str) for host in self.allowed_hosts):
            raise ValueError("mcp_allowed_hosts_invalid")
        hosts = tuple(host.lower().rstrip(".") for host in self.allowed_hosts)
        if any(not host or "/" in host or ":" in host or "@" in host for host in hosts):
            raise ValueError("mcp_allowed_hosts_invalid")
        if endpoint_host.lower().rstrip(".") not in hosts:
            raise ValueError("mcp_endpoint_host_not_allowlisted")
        if not isinstance(self.required_scopes, tuple) or len(self.required_scopes) > 64:
            raise ValueError("mcp_required_scopes_invalid")
        if any(not isinstance(scope, str) or not scope or len(scope) > 128 for scope in self.required_scopes):
            raise ValueError("mcp_required_scopes_invalid")
        if len(set(self.required_scopes)) != len(self.required_scopes):
            raise ValueError("mcp_required_scopes_duplicate")
        object.__setattr__(self, "allowed_hosts", hosts)


class MCPRuntimeService:
    """Build policy-bound clients from host-owned dependencies only.

    Provider managers and endpoint configuration must be prepared by trusted
    startup code. The authenticated owner ID is supplied by the API session;
    model input is never an identity source.
    """

    def __init__(
        self,
        *,
        connection_managers: Mapping[str, OAuthMCPConnectionManager],
        provider_configs: Mapping[str, MCPProviderConnectionConfig],
        policy_store: SQLiteMCPToolPolicyStore,
        audit_log: SQLiteApprovalAuditLog,
    ):
        if not isinstance(connection_managers, Mapping) or not isinstance(provider_configs, Mapping):
            raise TypeError("trusted_mcp_provider_mappings_required")
        if not connection_managers or set(connection_managers) != set(provider_configs):
            raise ValueError("mcp_provider_manager_config_mismatch")
        if not isinstance(policy_store, SQLiteMCPToolPolicyStore):
            raise TypeError("durable_mcp_policy_store_required")
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("durable_mcp_audit_log_required")
        for provider_id, manager in connection_managers.items():
            if not isinstance(provider_id, str) or not provider_id.strip():
                raise ValueError("mcp_provider_id_invalid")
            if not isinstance(manager, OAuthMCPConnectionManager):
                raise TypeError("trusted_oauth_mcp_connection_manager_required")
            if not isinstance(provider_configs[provider_id], MCPProviderConnectionConfig):
                raise TypeError("trusted_mcp_provider_config_required")
            if getattr(manager, "_provider_id", None) != provider_id:
                raise ValueError("mcp_manager_provider_identity_mismatch")
        self._managers = MappingProxyType(dict(connection_managers))
        self._configs = MappingProxyType(dict(provider_configs))
        self._policies = policy_store
        self._audit = audit_log

    def create_client(
        self,
        *,
        owner_id: str,
        provider_id: str,
        account_id: str,
        granted_scopes,
        owner_approval,
        timeout: float = 10,
    ):
        """Create one live policy-bound client; unknown or stale policy fails closed."""
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("authenticated_owner_required")
        if not isinstance(provider_id, str) or provider_id not in self._managers:
            raise ValueError("mcp_provider_not_configured")
        if not isinstance(account_id, str) or not account_id.strip() or len(account_id) > 256:
            raise ValueError("provider_account_required")
        if not callable(owner_approval):
            raise ValueError("owner_approval_callback_required")
        config = self._configs[provider_id]
        required = set(config.required_scopes)
        if not isinstance(granted_scopes, (tuple, list, set, frozenset)):
            raise ValueError("granted_oauth_scopes_required")
        if len(granted_scopes) > 64 or any(
            not isinstance(scope, str) or not scope or len(scope) > 128
            for scope in granted_scopes
        ):
            raise ValueError("granted_oauth_scopes_invalid")
        normalized_scopes = tuple(granted_scopes)
        if not required.issubset(set(normalized_scopes)):
            raise ValueError("required_oauth_scope_missing")

        registry = self._policies.load_registry(
            owner_id=owner_id, provider_id=provider_id, account_id=account_id,
        )
        policy_audit = MCPToolPolicyAudit(self._audit)
        execution_audit = MCPExecutionAudit(
            self._audit, owner_id=owner_id,
            provider_id=provider_id, account_id=account_id,
        )

        def current_revision():
            return self._policies.current_revision(
                owner_id=owner_id, provider_id=provider_id, account_id=account_id,
            )

        return self._managers[provider_id].create_policy_client(
            owner_id=owner_id,
            account_id=account_id,
            endpoint=config.endpoint,
            allowed_hosts=config.allowed_hosts,
            policy_registry=registry,
            granted_scopes=normalized_scopes,
            owner_approval=owner_approval,
            audit=policy_audit,
            current_revision=current_revision,
            execution_audit=execution_audit,
            timeout=timeout,
        )
