import unittest

from core.network_firewall import FirewallPolicyError, NetworkFirewall, build_network_firewall_from_config


class NetworkFirewallConfigTests(unittest.TestCase):
    def test_disabled_configuration_does_not_create_a_policy(self):
        self.assertIsNone(build_network_firewall_from_config({"firewall_enabled": False}))

    def test_enabled_configuration_unions_web_and_mcp_hosts(self):
        audited = []
        firewall = build_network_firewall_from_config(
            {"firewall_enabled": True, "web_allowed_hosts": ["reader.example.com"],
             "mcp_servers": [{"allowed_hosts": ["mcp-one.example.com", "MCP-TWO.example.com"]}]},
            audit=audited.append,
        )
        self.assertIsInstance(firewall, NetworkFirewall)
        self.assertEqual(firewall.allowed_hosts, frozenset({
            "reader.example.com", "mcp-one.example.com", "mcp-two.example.com",
        }))
        decision = firewall.authorize(host="reader.example.com", port=443,
                                      resolved_addresses=("93.184.216.34",))
        self.assertTrue(decision.allowed)
        self.assertEqual(audited, [decision])

    def test_enabled_configuration_includes_explicit_provider_hosts(self):
        firewall = build_network_firewall_from_config({
            "firewall_enabled": True,
            "provider_allowed_hosts": ["api.openai.com"],
        })
        self.assertEqual(firewall.allowed_hosts, frozenset({"api.openai.com"}))

    def test_enabled_configuration_rejects_malformed_provider_host_list(self):
        with self.assertRaisesRegex(FirewallPolicyError, "firewall_host_configuration_invalid"):
            build_network_firewall_from_config({
                "firewall_enabled": True,
                "provider_allowed_hosts": "api.openai.com",
            })

    def test_enabled_configuration_requires_explicit_hosts(self):
        with self.assertRaisesRegex(FirewallPolicyError, "requires_configured_hosts"):
            build_network_firewall_from_config({"firewall_enabled": True})

    def test_invalid_enable_flag_and_host_shapes_are_rejected(self):
        configs = (
            {"firewall_enabled": "true", "web_allowed_hosts": ["api.example.com"]},
            {"firewall_enabled": True, "web_allowed_hosts": "api.example.com"},
            {"firewall_enabled": True, "mcp_servers": [None]},
        )
        for config in configs:
            with self.subTest(config=config), self.assertRaises(FirewallPolicyError):
                build_network_firewall_from_config(config)

    def test_invalid_hosts_are_rejected_by_firewall_constructor(self):
        with self.assertRaises(FirewallPolicyError):
            build_network_firewall_from_config(
                {"firewall_enabled": True, "web_allowed_hosts": ["*.example.com"]},
            )


if __name__ == "__main__":
    unittest.main()
