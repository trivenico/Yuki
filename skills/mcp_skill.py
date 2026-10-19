"""Bounded MCP tool skill wrapper for Yuki's skill registry."""
from core.mcp_client import MCPHTTPClient, MCPError
from skills.base import Skill, SkillResult
from skills.safe_local import parse_object


class MCPToolSkill(Skill):
    name = "mcp_tool"
    description = "Execute an allow-listed MCP tool call over HTTPS with human approval."
    risk = "high"

    def __init__(self, allowed_hosts=(), allowed_tools=(), approver=None):
        self.allowed_hosts = allowed_hosts
        self.allowed_tools = allowed_tools
        self.approver = approver

    def run(self, argument=""):
        obj = parse_object(argument)
        if not obj or not isinstance(obj.get("endpoint"), str) or not isinstance(obj.get("tool"), str):
            return SkillResult(False, 'Provide argument JSON: {"endpoint":"https://mcp.example.com","tool":"tool_name","arguments":{}}')

        endpoint = obj["endpoint"]
        tool_name = obj["tool"]
        tool_args = obj.get("arguments", {})
        if not isinstance(tool_args, dict):
            return SkillResult(False, "Tool arguments must be a dictionary object.")

        try:
            client = MCPHTTPClient(
                endpoint=endpoint,
                allowed_hosts=self.allowed_hosts,
                allowed_tools=self.allowed_tools,
                approver=self.approver,
            )
            client.initialize()
            res = client.call_tool(tool_name, tool_args)
            return SkillResult(True, f"MCP tool '{tool_name}' executed.", res)
        except MCPError as exc:
            return SkillResult(False, f"MCP skill call failed: {exc}")
        except Exception as exc:
            return SkillResult(False, f"MCP skill call failed safely: {exc}")
