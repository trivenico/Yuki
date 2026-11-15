import unittest

from core.network_firewall import FirewallPolicyError, NetworkFirewall


class NetworkFirewallTests(unittest.TestCase):
    def setUp(self):
        self.firewall = NetworkFirewall(allowed_hosts=("api.example.com",))

    def test_allows_exact_allowlisted_host_and_public_destination(self):
        decision = self.firewall.authorize(
            host="API.EXAMPLE.COM.", port=443, resolved_addresses=("93.184.216.34",),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "allowed")

    def test_denies_unknown_host_and_non_allowlisted_port(self):
        self.assertEqual(self.firewall.authorize(
            host="other.example.com", port=443, resolved_addresses=("93.184.216.34",),
        ).reason, "host_not_allowlisted")
        self.assertEqual(self.firewall.authorize(
            host="api.example.com", port=8443, resolved_addresses=("93.184.216.34",),
        ).reason, "port_not_allowlisted")

    def test_denies_private_loopback_reserved_and_mixed_dns_answers(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.2", "2001:db8::1"):
            with self.subTest(address=address):
                self.assertFalse(self.firewall.authorize(
                    host="api.example.com", port=443, resolved_addresses=(address,),
                ).allowed)
        self.assertFalse(self.firewall.authorize(
            host="api.example.com", port=443,
            resolved_addresses=("93.184.216.34", "10.0.0.1"),
        ).allowed)

    def test_denies_missing_or_malformed_dns_answers(self):
        for addresses in ((), ("not-an-ip",),):
            with self.subTest(addresses=addresses):
                self.assertFalse(self.firewall.authorize(
                    host="api.example.com", port=443, resolved_addresses=addresses,
                ).allowed)

    def test_audit_failure_fails_closed(self):
        firewall = NetworkFirewall(
            allowed_hosts=("api.example.com",),
            audit=lambda decision: (_ for _ in ()).throw(RuntimeError("audit offline")),
        )
        result = firewall.authorize(
            host="api.example.com", port=443, resolved_addresses=("93.184.216.34",),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "audit_unavailable")

    def test_invalid_allowlist_configuration_is_rejected(self):
        for hosts in ((), ("*.example.com",), ("127.0.0.1",), ("bad host",)):
            with self.subTest(hosts=hosts), self.assertRaises(FirewallPolicyError):
                NetworkFirewall(allowed_hosts=hosts)
        with self.assertRaises(FirewallPolicyError):
            NetworkFirewall(allowed_hosts=("api.example.com",), allowed_ports=(0,))


if __name__ == "__main__":
    unittest.main()
