import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class MainFirewallCompositionTests(unittest.TestCase):
    def test_build_agent_passes_shared_firewall_to_default_skills(self):
        policy = object()
        config = {
            "workspace": tempfile.mkdtemp(),
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
