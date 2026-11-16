import tempfile
import unittest
from unittest.mock import patch

import main
from core.network_firewall import NetworkFirewall


class MainFirewallCompositionTests(unittest.TestCase):
    def test_default_mcp_skills_share_the_application_firewall(self):
        policy = NetworkFirewall(allowed_hosts={"mcp.example.com"})
        with tempfile.TemporaryDirectory() as directory:
            registry = main.SkillRegistry.defaults(
                directory,
                mcp_servers=[{
                    "name": "docs",
                    "endpoint": "https://mcp.example.com/mcp",
                    "allowed_hosts": ["mcp.example.com"],
                    "allowed_tools": ["search_docs"],
                }],
                firewall_policy=policy,
            )
        self.assertIs(registry.get("mcp_plugin").firewall_policy, policy)
        self.assertIs(registry.get("connector_management").firewall_policy, policy)

    def test_build_agent_passes_shared_firewall_to_default_skills(self):
        policy = object()
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "workspace": directory,
                "gmail_enabled": False,
                "web_allowed_hosts": ("example.com",),
                "plugin_discovery_enabled": False,
                "trusted_plugin_modules": (),
                "github_allowed_roots": [],
                "mcp_servers": [],
            }
            with patch.object(main.SkillRegistry, "defaults", return_value=object()) as defaults:
                with patch.object(main, "YukiCoreAgent", return_value=object()):
                    main.build_agent(config, firewall_policy=policy)

            self.assertIs(defaults.call_args.kwargs["firewall_policy"], policy)


if __name__ == "__main__":
    unittest.main()
