import json
import unittest
from unittest.mock import patch
from skills.connector_management import ConnectorManagementSkill

SERVER = {"name":"docs","endpoint":"https://mcp.example.com/mcp",
          "allowed_hosts":["mcp.example.com"],"allowed_tools":["search_docs"]}

class ConnectorManagementTests(unittest.TestCase):
    def test_list_is_local_and_does_not_connect(self):
        skill = ConnectorManagementSkill([SERVER])
        with patch("skills.connector_management.MCPHTTPClient") as factory:
            result = skill.run(json.dumps({"action":"list"}))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["connectors"][0]["host"], "mcp.example.com")
        factory.assert_not_called()

    def test_check_only_initializes_and_lists_tools(self):
        skill = ConnectorManagementSkill([SERVER])
        with patch("skills.connector_management.MCPHTTPClient") as factory:
            client = factory.return_value
            client.initialize.return_value = {"server_info":{"name":"Docs","version":"1"},"protocol_version":"2025-03-26"}
            client.list_tools.return_value = [{"name":"search_docs"},{"name":"delete_all"}]
            result = skill.run(json.dumps({"action":"check","server":"docs"}))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["missing_allowlisted_tools"], [])
        self.assertEqual(result.data["unexpected_tools_not_executable"], ["delete_all"])
        client.call_tool.assert_not_called()

    def test_unknown_connector_fails_closed(self):
        result = ConnectorManagementSkill([SERVER]).run(json.dumps({"action":"check","server":"unknown"}))
        self.assertFalse(result.ok)

    def test_duplicate_connector_rejected(self):
        with self.assertRaises(ValueError):
            ConnectorManagementSkill([SERVER, SERVER])

    def test_unknown_action_fails_closed(self):
        result = ConnectorManagementSkill([SERVER]).run(json.dumps({"action":"install","server":"docs"}))
        self.assertFalse(result.ok)

if __name__ == "__main__":
    unittest.main()
