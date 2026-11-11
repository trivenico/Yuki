"""Owner-scoped, non-executable MCP tool discovery.

Server-provided names, descriptions and input schemas are untrusted metadata.
Discovery never classifies a tool as read-only or grants execution permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from types import MappingProxyType

from core.prompt_guard import scan_untrusted_text

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_MAX_SCHEMA_BYTES = 16_384
_MAX_SCHEMA_DEPTH = 8
_MAX_SCHEMA_ITEMS = 256


class MCPToolDiscoveryError(RuntimeError):
    """Stable discovery error without remote metadata disclosure."""


@dataclass(frozen=True)
class DiscoveredMCPTool:
    name: str
    description: str
    input_schema: object
    metadata_trust: str = "untrusted_remote_server"
    read_only: bool = False
    execution_enabled: bool = False


@dataclass(frozen=True)
class MCPToolDiscoverySnapshot:
    owner_id: str
    provider_id: str
    account_id: str
    server_info: object
    protocol_version: str
    tools: tuple[DiscoveredMCPTool, ...]
    tools_trust: str = "untrusted_remote_server"
    execution_enabled: bool = False


def _bounded_json(value, *, depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > _MAX_SCHEMA_ITEMS or depth > _MAX_SCHEMA_DEPTH:
        raise MCPToolDiscoveryError("tool_schema_exceeds_limits")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 2048:
            raise MCPToolDiscoveryError("tool_schema_exceeds_limits")
        return value
    if isinstance(value, list):
        return [_bounded_json(item, depth=depth + 1, budget=budget) for item in value]
    if isinstance(value, dict):
        if len(value) > _MAX_SCHEMA_ITEMS:
            raise MCPToolDiscoveryError("tool_schema_exceeds_limits")
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise MCPToolDiscoveryError("tool_schema_invalid")
            result[key] = _bounded_json(item, depth=depth + 1, budget=budget)
        return result
    raise MCPToolDiscoveryError("tool_schema_invalid")


def discover_mcp_tools(client, *, owner_id: str, provider_id: str, account_id: str):
    """Initialize an already owner-bound client and return inert tool metadata.

    The client must be created by the host's OAuth connection manager using
    owner/provider/account values from trusted application state. Its execution
    allow-list should be empty for initial discovery.
    """
    for label, value in (
        ("owner_id", owner_id), ("provider_id", provider_id), ("account_id", account_id),
    ):
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise MCPToolDiscoveryError("discovery_identity_invalid")
    if not callable(getattr(client, "initialize", None)) or not callable(
        getattr(client, "list_tools", None)
    ):
        raise MCPToolDiscoveryError("mcp_discovery_client_invalid")
    try:
        initialized = client.initialize()
        remote_tools = client.list_tools()
    except Exception:
        raise MCPToolDiscoveryError("mcp_tool_discovery_failed") from None
    if not isinstance(initialized, dict) or not isinstance(remote_tools, list):
        raise MCPToolDiscoveryError("mcp_tool_discovery_response_invalid")
    if len(remote_tools) > 200:
        raise MCPToolDiscoveryError("mcp_tool_discovery_response_invalid")

    tools = []
    seen = set()
    for item in remote_tools:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name) or name in seen:
            continue
        description = item.get("description", "")
        if not isinstance(description, str):
            description = ""
        description = description[:500]
        schema = _bounded_json(item.get("input_schema", {}))
        try:
            encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            raise MCPToolDiscoveryError("tool_schema_invalid") from None
        if len(encoded) > _MAX_SCHEMA_BYTES:
            raise MCPToolDiscoveryError("tool_schema_exceeds_limits")
        # Scan metadata for visibility, but never treat a clean scan as trust.
        scan_untrusted_text(description + " " + encoded.decode("utf-8"))
        tools.append(DiscoveredMCPTool(
            name=name, description=description, input_schema=MappingProxyType(schema)
            if isinstance(schema, dict) else tuple(schema) if isinstance(schema, list) else schema,
        ))
        seen.add(name)

    server_info = initialized.get("server_info", {})
    if not isinstance(server_info, dict):
        server_info = {}
    safe_server_info = {
        str(key)[:64]: str(value)[:128]
        for key, value in list(server_info.items())[:16]
        if isinstance(key, str) and isinstance(value, (str, int, float, bool))
    }
    protocol = initialized.get("protocol_version", "")
    if not isinstance(protocol, str):
        protocol = ""
    return MCPToolDiscoverySnapshot(
        owner_id=owner_id,
        provider_id=provider_id,
        account_id=account_id,
        server_info=MappingProxyType(safe_server_info),
        protocol_version=protocol[:64],
        tools=tuple(tools),
    )
