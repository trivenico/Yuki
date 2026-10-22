"""Safe catalog and health diagnostics for explicitly configured MCP connectors.

This skill never installs code, changes configuration, or invokes remote tools.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit

from core.mcp_client import MCPHTTPClient, MCPError
from skills.base import Skill, SkillResult

class ConnectorManagementSkill(Skill):
    name = "connector_management"
    description = "List configured MCP connectors and perform a bounded read-only health check without invoking remote tools."
    risk = "medium"
    requires_approval = True
    max_argument_length = 2000

    def __init__(self, servers=(), timeout=8):
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 15:
            raise ValueError("timeout must be from 1 to 15 seconds.")
        self.timeout = timeout
        self.servers = {}
        for item in servers:
            if not isinstance(item, dict):
                raise ValueError("Each connector must be a mapping.")
            name = item.get("name")
            endpoint = item.get("endpoint")
            hosts = item.get("allowed_hosts")
            tools = item.get("allowed_tools")
            if not isinstance(name, str) or not name or name in self.servers:
                raise ValueError("Connector names must be non-empty and unique.")
            if not isinstance(endpoint, str) or len(endpoint) > 2048:
                raise ValueError("Connector endpoint must be bounded text.")
            if not isinstance(hosts, list) or not hosts or any(not isinstance(h, str) for h in hosts):
                raise ValueError("Connector host allow-list is required.")
            if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
                raise ValueError("Connector tool allow-list must be a list.")
            self.servers[name] = {"endpoint": endpoint, "allowed_hosts": tuple(hosts),
                                  "allowed_tools": tuple(tools)}

    @staticmethod
    def _summary(name, config):
        parts = urlsplit(config["endpoint"])
        return {"name": name, "enabled": True, "transport": "https",
                "host": (parts.hostname or "")[:253],
                "allowlisted_tool_count": len(config["allowed_tools"]),
                "allowed_tools": list(config["allowed_tools"])[:100],
                "credentials_configured": False,
                "note": "Authentication is not configured by this connector schema; configure credentials only through a supported secure mechanism."}

    def run(self, argument=""):
        try:
            request = json.loads(argument) if argument.strip() else {}
            if not isinstance(request, dict):
                raise ValueError("Argument must be a JSON object.")
            action = request.get("action", "list")
            if action == "list":
                return SkillResult(True, "Configured connectors listed. No network requests were made.",
                                   {"connectors": [self._summary(name, cfg) for name, cfg in sorted(self.servers.items())]})
            if action != "check":
                raise ValueError("action must be list or check.")
            name = request.get("server")
            config = self.servers.get(name)
            if config is None:
                raise ValueError("Connector is not configured.")
            client = MCPHTTPClient(config["endpoint"], allowed_hosts=config["allowed_hosts"],
                                   allowed_tools=config["allowed_tools"], timeout=self.timeout)
            info = client.initialize()
            tools = client.list_tools()
            exposed = {item["name"] for item in tools}
            approved = set(config["allowed_tools"])
            return SkillResult(True, "Read-only connector health check completed; no remote tools were invoked.",
                {"server": name, "healthy": True,
                 "server_info": {k: str(v)[:120] for k, v in info.get("server_info", {}).items()
                                 if k in {"name", "version", "title"}},
                 "protocol_version": str(info.get("protocol_version", ""))[:40],
                 "advertised_tool_count": len(tools),
                 "allowlisted_tool_count": len(approved),
                 "missing_allowlisted_tools": sorted(approved - exposed)[:100],
                 "unexpected_tools_not_executable": sorted(exposed - approved)[:100]})
        except (ValueError, TypeError, MCPError, json.JSONDecodeError) as exc:
            return SkillResult(False, f"Connector operation failed safely ({type(exc).__name__}): {str(exc)[:200]}")
        except Exception as exc:
            return SkillResult(False, f"Connector operation failed safely ({type(exc).__name__}).")
