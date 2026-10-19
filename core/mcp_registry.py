"""Multi-server MCP catalog with namespaced tools and approval-gated invocation."""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlsplit
from typing import Any
from core.mcp_client import MCPHTTPClient, MCPError

@dataclass(frozen=True)
class MCPTool:
    qualified_name: str
    server_name: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]

class MCPRegistry:
    """Discover tools from configured servers; discovery alone grants no execution rights."""
    def __init__(self, servers, *, approver):
        if not isinstance(servers, list) or len(servers) > 25:
            raise ValueError("servers must be a list of at most 25 configurations")
        self._servers = {}
        self._tools = {}
        self._approver = approver
        for item in servers:
            if not isinstance(item, dict): raise ValueError("MCP server configuration must be an object")
            name, endpoint = item.get("name"), item.get("endpoint")
            if not isinstance(name,str) or not name.strip() or name in self._servers: raise ValueError("MCP server names must be unique and non-empty")
            parts = urlsplit(endpoint) if isinstance(endpoint,str) else None
            if parts is None or parts.scheme != "https" or not parts.hostname or parts.username or parts.password: raise ValueError("MCP endpoints must be HTTPS without credentials")
            allowed = item.get("allowed_tools", [])
            if not isinstance(allowed,list) or any(not isinstance(v,str) or not v.strip() for v in allowed): raise ValueError("allowed_tools must be a list of names")
            self._servers[name] = {"endpoint":endpoint,"host":parts.hostname,"allowed_tools":frozenset(allowed)}
    def discover(self):
        discovered = {}
        for server_name, config in self._servers.items():
            client = MCPHTTPClient(config["endpoint"], allowed_hosts=(config["host"],), allowed_tools=config["allowed_tools"], approver=self._approver)
            client.initialize()
            for item in client.list_tools():
                remote = item["name"]
                qualified = f"mcp.{server_name}.{remote}"
                discovered[qualified] = MCPTool(qualified,server_name,remote,item["description"],item["input_schema"] if isinstance(item["input_schema"],dict) else {})
        self._tools = discovered
        return [{"name":v.qualified_name,"server":v.server_name,"description":v.description,"input_schema":v.input_schema,
                 "allowed":v.remote_name in self._servers[v.server_name]["allowed_tools"]} for v in discovered.values()]
    def invoke(self, qualified_name, arguments):
        tool = self._tools.get(qualified_name)
        if tool is None: raise MCPError("MCP tool must be discovered before invocation.")
        config = self._servers[tool.server_name]
        if tool.remote_name not in config["allowed_tools"]: raise MCPError("MCP tool is not in the explicit local allow-list.")
        client = MCPHTTPClient(config["endpoint"],allowed_hosts=(config["host"],),allowed_tools=config["allowed_tools"],approver=self._approver)
        client.initialize()
        return client.call_tool(tool.remote_name,arguments)
