import socket
import unittest

from automation.browser import BrowserAutomationError, SafeBrowser
from core.network_firewall import NetworkFirewall


def answer(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


class BrowserFirewallTests(unittest.TestCase):
    def resolver_for(self, mapping):
        def resolve(host, port, type=socket.SOCK_STREAM):
            self.assertEqual(port, 443)
            self.assertEqual(type, socket.SOCK_STREAM)
            return [answer(ip) for ip in mapping[host]]
        return resolve

    def test_authorizes_and_pins_each_allowed_host(self):
        browser = SafeBrowser(
            allowed_hosts=("example.com", "static.example.com"),
            resolver=self.resolver_for({
                "example.com": ["93.184.216.34", "1.1.1.1"],
                "static.example.com": ["8.8.8.8"],
            }),
        )
        rules = browser._resolve_authorized_hosts()
        self.assertIn("MAP example.com 1.1.1.1", rules)
        self.assertIn("MAP static.example.com 8.8.8.8", rules)

    def test_rejects_private_or_mixed_dns_answers(self):
        for values in (["127.0.0.1"], ["93.184.216.34", "10.0.0.2"]):
            with self.subTest(values=values):
                browser = SafeBrowser(
                    allowed_hosts=("example.com",),
                    resolver=self.resolver_for({"example.com": values}),
                )
                with self.assertRaises(BrowserAutomationError):
                    browser._resolve_authorized_hosts()

    def test_firewall_audit_failure_blocks_browser_resolution(self):
        firewall = NetworkFirewall(
            allowed_hosts=("example.com",),
            audit=lambda decision: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
        )
        browser = SafeBrowser(
            allowed_hosts=("example.com",),
            resolver=self.resolver_for({"example.com": ["93.184.216.34"]}),
            firewall_policy=firewall,
        )
        with self.assertRaisesRegex(BrowserAutomationError, "network firewall"):
            browser._resolve_authorized_hosts()

    def test_rejects_unallowlisted_hosts_and_non_https_ports(self):
        browser = SafeBrowser(
            allowed_hosts=("example.com",),
            resolver=self.resolver_for({"example.com": ["93.184.216.34"]}),
        )
        for url in ("https://attacker.example/", "http://example.com/",
                    "https://example.com:8443/", "https://user@example.com/"):
            with self.subTest(url=url), self.assertRaises(BrowserAutomationError):
                browser._validate(url)

    def test_rejects_invalid_firewall_policy_at_construction(self):
        restrictive = NetworkFirewall(
            allowed_hosts=("example.com",), allowed_ports=(8443,),
        )
        with self.assertRaises(ValueError):
            SafeBrowser(allowed_hosts=("example.com",), firewall_policy=restrictive)


if __name__ == "__main__":
    unittest.main()
