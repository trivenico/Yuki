import unittest
from unittest.mock import patch

from automation.browser import BrowserAutomationError, SafeBrowser


class SafeBrowserURLPolicyTests(unittest.TestCase):
    def setUp(self):
        self.browser = SafeBrowser(allowed_hosts={"docs.example.com"})

    def test_allows_exact_https_host_on_default_port(self):
        self.assertEqual(
            self.browser._parse_allowed_url("https://docs.example.com/guide"),
            "docs.example.com",
        )

    def test_rejects_nonstandard_port_for_every_browser_request(self):
        with self.assertRaises(BrowserAutomationError):
            self.browser._parse_allowed_url("https://docs.example.com:8443/private")

    def test_rejects_userinfo_even_when_hostname_is_allowlisted(self):
        with self.assertRaises(BrowserAutomationError):
            self.browser._parse_allowed_url("https://attacker@docs.example.com/")

    def test_rejects_http_and_unlisted_hosts(self):
        for url in ("http://docs.example.com/", "https://other.example.com/"):
            with self.subTest(url=url), self.assertRaises(BrowserAutomationError):
                self.browser._parse_allowed_url(url)

    def test_malformed_port_is_normalized(self):
        with self.assertRaises(BrowserAutomationError):
            self.browser._parse_allowed_url("https://docs.example.com:notaport/")

    def test_dns_answers_must_all_be_public(self):
        record = lambda address: (None, None, None, None, (address, 443))
        with patch("automation.browser.socket.getaddrinfo", return_value=[
            record("8.8.8.8"), record("127.0.0.1")
        ]):
            with self.assertRaises(BrowserAutomationError):
                self.browser._validate("https://docs.example.com/")
