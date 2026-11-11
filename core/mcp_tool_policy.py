"""Host-reviewed, owner/account-bound MCP tool execution policy.

Remote MCP metadata is never an authorization source. Policies are constructed
from trusted host configuration and replaced atomically as immutable snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
from types import MappingProxyType


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")
_MAX_ARGUMENT_BYTES = 64_000


class MCPToolPolicyError(ValueError):
    """Invalid trusted MCP policy configuration."""


class MCPToolRisk(str, Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    FINANCIAL = "financial"


@dataclass(frozen=True)
class MCPToolPolicy:
    owner_id: str
    provider_id: str
    account_id: str
    tool_name: str
    risk: MCPToolRisk
    required_scopes: tuple[str, ...]
    version: int
    enabled: bool = True

    def __post_init__(self):
        for label, value in (
            ("owner_id", self.owner_id), ("provider_id", self.provider_id),
            ("account_id", self.account_id),
        ):
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise MCPToolPolicyError(f"{label}_invalid")
        if not isinstance(self.tool_name, str) or not _TOOL_NAME.fullmatch(self.tool_name):
            raise MCPToolPolicyError("tool_name_invalid")
        if not isinstance(self.risk, MCPToolRisk):
            raise MCPToolPolicyError("trusted_risk_class_required")
        if not isinstance(self.required_scopes, tuple) or len(self.required_scopes) > 64:
            raise MCPToolPolicyError("required_scopes_invalid")
        if any(not isinstance(scope, str) or not _SCOPE.fullmatch(scope)
               for scope in self.required_scopes):
            raise MCPToolPolicyError("required_scopes_invalid")
        if len(set(self.required_scopes)) != len(self.required_scopes):
            raise MCPToolPolicyError("required_scopes_duplicate")
        if type(self.version) is not int or self.version < 1:
            raise MCPToolPolicyError("policy_version_invalid")
        if type(self.enabled) is not bool:
            raise MCPToolPolicyError("policy_enabled_invalid")


@dataclass(frozen=True)
class MCPToolApprovalRequest:
    owner_id: str
    provider_id: str
    account_id: str
    tool_name: str
    risk: MCPToolRisk
    policy_version: int
    arguments: object


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class MCPToolPolicyRegistry:
    """Immutable policy snapshot; unknown or stale identities are denied."""

    def __init__(self, *, owner_id: str, policies, revision: int):
        if not isinstance(owner_id, str) or not _IDENTIFIER.fullmatch(owner_id):
            raise MCPToolPolicyError("owner_id_invalid")
        if type(revision) is not int or revision < 1:
            raise MCPToolPolicyError("registry_revision_invalid")
        if not isinstance(policies, (tuple, list)) or len(policies) > 1000:
            raise MCPToolPolicyError("policies_invalid")
        indexed = {}
        for policy in policies:
            if not isinstance(policy, MCPToolPolicy) or policy.owner_id != owner_id:
                raise MCPToolPolicyError("policy_owner_mismatch")
            key = (policy.provider_id, policy.account_id, policy.tool_name)
            if key in indexed:
                raise MCPToolPolicyError("duplicate_tool_policy")
            indexed[key] = policy
        self.owner_id = owner_id
        self.revision = revision
        self._policies = MappingProxyType(indexed)

    def get(self, *, provider_id: str, account_id: str, tool_name: str):
        return self._policies.get((provider_id, account_id, tool_name))

    def tool_names(self, *, provider_id: str, account_id: str) -> tuple[str, ...]:
        """Return only enabled names for this exact provider/account binding."""
        return tuple(sorted(
            policy.tool_name for policy in self._policies.values()
            if policy.provider_id == provider_id
            and policy.account_id == account_id
            and policy.enabled
        ))

    @property
    def policy_count(self):
        return len(self._policies)

    def build_approver(
        self, *, owner_id: str, provider_id: str, account_id: str,
        granted_scopes, owner_approval, audit, current_revision,
    ):
        """Return the bool callback expected by MCPHTTPClient.

        Read-only calls require an exact enabled policy and granted scopes.
        Write and financial calls additionally require a fresh literal-True
        owner confirmation for every invocation. Audit failure denies access.
        """
        if owner_id != self.owner_id or not isinstance(owner_id, str):
            raise MCPToolPolicyError("authenticated_owner_mismatch")
        for label, value in (("provider_id", provider_id), ("account_id", account_id)):
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise MCPToolPolicyError(f"{label}_invalid")
        if not isinstance(granted_scopes, (tuple, list, set, frozenset)):
            raise MCPToolPolicyError("granted_scopes_invalid")
        if any(not isinstance(scope, str) or not _SCOPE.fullmatch(scope)
               for scope in granted_scopes):
            raise MCPToolPolicyError("granted_scopes_invalid")
        scopes = frozenset(granted_scopes)
        if not callable(owner_approval) or not callable(audit) or not callable(current_revision):
            raise MCPToolPolicyError("owner_approval_audit_and_revision_required")

        def approve(tool_name, arguments):
            if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
                return False
            if not isinstance(arguments, dict):
                return False
            try:
                encoded = json.dumps(
                    arguments, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeEncodeError):
                return False
            if len(encoded) > _MAX_ARGUMENT_BYTES:
                return False
            try:
                if current_revision() != self.revision:
                    return False
            except Exception:
                return False
            policy = self.get(
                provider_id=provider_id, account_id=account_id, tool_name=tool_name,
            )
            if policy is None or not policy.enabled:
                return False
            if not set(policy.required_scopes).issubset(scopes):
                return False

            base = {
                "owner_id": owner_id,
                "provider_id": provider_id,
                "account_id": account_id,
                "tool_name": tool_name,
                "risk": policy.risk.value,
                "policy_version": policy.version,
                "registry_revision": self.revision,
            }
            try:
                audit({**base, "event": "requested"})
            except Exception:
                return False

            if policy.risk is MCPToolRisk.READ_ONLY:
                try:
                    audit({**base, "event": "authorized_read_only"})
                    return True
                except Exception:
                    return False

            request = MCPToolApprovalRequest(
                owner_id=owner_id,
                provider_id=provider_id,
                account_id=account_id,
                tool_name=tool_name,
                risk=policy.risk,
                policy_version=policy.version,
                arguments=_freeze(json.loads(encoded.decode("utf-8"))),
            )
            try:
                confirmed = owner_approval(request) is True
            except Exception:
                confirmed = False
            try:
                audit({**base, "event": "owner_approved" if confirmed else "denied"})
            except Exception:
                return False
            return confirmed

        return approve
