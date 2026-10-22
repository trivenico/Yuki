import json
import unittest
from unittest.mock import MagicMock, patch
from skills.mcp_plugin import MCPPluginSkill

SERVER = {"name":"docs","endpoint":"https://mcp.example.com/mcp",
          "allowed_hosts":["mcp.example.com"],"allowed_tools":["search_docs"]}

class MCPPluginSkillTests(unittest.TestCase):
    def test_rejects_duplicate_or_invalid_server_config(self):
        with self.assertRaises(ValueError):
            MCPPluginSkill([SERVER, SERVER])
        with self.assertRaises(ValueError):
            MCPPluginSkill([{"name":"docs","endpoint":"https://mcp.example.com","allowed_hosts":[],"allowed_tools":[]}])

    def test_requires_explicit_configured_server(self):
        result = MCPPluginSkill([]).run(json.dumps({"action":"list","server":"docs"}))
        self.assertFalse(result.ok)

    @patch("skills.mcp_plugin.MCPHTTPClient")
    def test_list_only_returns_allowlisted_tools(self, factory):
        client = factory.return_value
        client.list_tools.return_value = [{"name":"search_docs","description":"search","input_schema":{}},
                                          {"name":"delete_all","description":"unsafe","input_schema":{}}]
        result = MCPPluginSkill([SERVER]).run(json.dumps({"action":"list","server":"docs"}))
        self.assertTrue(result.ok)
        self.assertEqual([t["name"] for t in result.data["tools"]], ["search_docs"])
        client.initialize.assert_called_once()
        client.call_tool.assert_not_called()

    @patch("skills.mcp_plugin.MCPHTTPClient")
    def test_call_requires_exact_confirmation_and_allowlisted_tool(self, factory):
        client = factory.return_value
        client.call_tool.return_value = {"result":{"ok":True},"untrusted":True}
        skill = MCPPluginSkill([SERVER])
        bad = skill.run(json.dumps({"action":"call","server":"docs","tool":"search_docs","arguments":{}}))
        self.assertFalse(bad.ok)
        client.call_tool.assert_not_called()
        good = skill.run(json.dumps({"action":"call","server":"docs","tool":"search_docs",
                                     "arguments":{"q":"hello"},"confirm":"CALL docs/search_docs"}))
        self.assertTrue(good.ok)
        client.call_tool.assert_called_once_with("search_docs", {"q":"hello"})

    def test_unknown_action_fails_closed(self):
        result = MCPPluginSkill([SERVER]).run(json.dumps({"action":"exec","server":"docs"}))
        self.assertFalse(result.ok)

if __name__ == "__main__":
    unittest.main()
