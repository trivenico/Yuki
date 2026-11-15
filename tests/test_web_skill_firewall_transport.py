import unittest
from unittest.mock import patch

from core.pinned_https_provider_transport import PinnedHTTPTransportError
from skills.web_skill import WebSkill, WebSkillError


class FakeResponse:
    def __init__(self, body=b"<html><title>Example</title><script>bad()</script><body>Hello world</body></html>",
                 status=200, content_type="text/html; charset=utf-8"):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def read(self, amount=-1):
        return self.body[:amount]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        if self.error:
            raise self.error
        return self.response


def build_skill(transport, **kwargs):
    with patch("skills.web_skill.PinnedHTTPSProviderTransport", return_value=transport):
        return WebSkill(**kwargs)


class WebSkillFirewallTransportTests(unittest.TestCase):
    def test_rejects_malformed_allowlist_entries_instead_of_sanitizing_them(self):
        invalid_allowlists = (
            "example.com",
            ("example.com..",),
            (" example.com",),
            ("example.com ",),
            ("*.example.com",),
            ("example..com",),
            ("example.com", "EXAMPLE.COM"),
            ("example.com", None),
        )
        for hosts in invalid_allowlists:
            with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                build_skill(FakeTransport(), allowed_hosts=hosts)

    def test_normalizes_one_terminal_root_dot_in_allowlist(self):
        skill = build_skill(FakeTransport(), allowed_hosts=("EXAMPLE.COM.",))
        self.assertEqual(skill.allowed_hosts, frozenset({"example.com"}))

    def test_constructor_does_not_accept_caller_supplied_transport(self):
        with self.assertRaises(TypeError):
            WebSkill(allowed_hosts=("example.com",), transport=FakeTransport())
    def test_fetch_uses_configured_pinned_transport_and_extracts_text(self):
        transport = FakeTransport()
        skill = build_skill(transport, allowed_hosts=("example.com",))
        result = skill.execute("https://example.com/docs")
        self.assertEqual(transport.calls, [("https://example.com/docs", 8)])
        self.assertIn("Hello world", result["text"])
        self.assertNotIn("bad()", result["text"])
        self.assertEqual(result["url"], "https://example.com/docs")
        self.assertTrue(transport.response.closed)

    def test_rejects_host_before_transport_dispatch(self):
        transport = FakeTransport()
        skill = build_skill(transport, allowed_hosts=("example.com",))
        with self.assertRaises(WebSkillError):
            skill.execute("https://attacker.example/docs")
        self.assertEqual(transport.calls, [])

    def test_rejects_non_https_and_non_default_ports(self):
        transport = FakeTransport()
        skill = build_skill(transport, allowed_hosts=("example.com",))
        for url in ("http://example.com/", "https://example.com:8443/", "https://user@example.com/"):
            with self.subTest(url=url), self.assertRaises(WebSkillError):
                skill.execute(url)
        self.assertEqual(transport.calls, [])

    def test_rejects_surrounding_whitespace_and_control_characters_before_transport(self):
        transport = FakeTransport()
        skill = build_skill(transport, allowed_hosts=("example.com",))
        invalid_urls = (
            " https://example.com/",
            "https://example.com/ ",
            "\nhttps://example.com/",
            "https://example.com/\t",
            "https://example.com/\n",
        )
        for url in invalid_urls:
            with self.subTest(url=repr(url)), self.assertRaises(WebSkillError):
                skill.execute(url)
        self.assertEqual(transport.calls, [])

    def test_rejects_multiple_trailing_dots_before_transport_dispatch(self):
        transport = FakeTransport()
        skill = build_skill(transport, allowed_hosts=("example.com",))
        with self.assertRaises(WebSkillError):
            skill.execute("https://example.com../")
        self.assertEqual(transport.calls, [])

    def test_firewall_transport_failure_is_reported_as_blocked(self):
        transport = FakeTransport(error=PinnedHTTPTransportError("cloud_destination_blocked"))
        skill = build_skill(transport, allowed_hosts=("example.com",))
        with self.assertRaisesRegex(WebSkillError, "network firewall"):
            skill.execute("https://example.com/")

    def test_rejects_oversized_and_non_text_responses(self):
        too_large = build_skill(
            FakeTransport(FakeResponse(body=b"12345")),
            allowed_hosts=("example.com",), max_bytes=4,
        )
        with self.assertRaisesRegex(WebSkillError, "size limit"):
            too_large.execute("https://example.com/")
        binary = build_skill(
            FakeTransport(FakeResponse(content_type="application/octet-stream")),
            allowed_hosts=("example.com",),
        )
        with self.assertRaisesRegex(WebSkillError, "HTML or plain-text"):
            binary.execute("https://example.com/")


if __name__ == "__main__":
    unittest.main()
