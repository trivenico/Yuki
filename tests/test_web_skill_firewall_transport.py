import unittest

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


class WebSkillFirewallTransportTests(unittest.TestCase):
    def test_fetch_uses_injected_firewall_transport_and_extracts_text(self):
        transport = FakeTransport()
        skill = WebSkill(allowed_hosts=("example.com",), transport=transport)
        result = skill.execute("https://example.com/docs")
        self.assertEqual(transport.calls, [("https://example.com/docs", 8)])
        self.assertIn("Hello world", result["text"])
        self.assertNotIn("bad()", result["text"])
        self.assertEqual(result["url"], "https://example.com/docs")
        self.assertTrue(transport.response.closed)

    def test_rejects_host_before_transport_dispatch(self):
        transport = FakeTransport()
        skill = WebSkill(allowed_hosts=("example.com",), transport=transport)
        with self.assertRaises(WebSkillError):
            skill.execute("https://attacker.example/docs")
        self.assertEqual(transport.calls, [])

    def test_rejects_non_https_and_non_default_ports(self):
        transport = FakeTransport()
        skill = WebSkill(allowed_hosts=("example.com",), transport=transport)
        for url in ("http://example.com/", "https://example.com:8443/", "https://user@example.com/"):
            with self.subTest(url=url), self.assertRaises(WebSkillError):
                skill.execute(url)
        self.assertEqual(transport.calls, [])

    def test_firewall_transport_failure_is_reported_as_blocked(self):
        transport = FakeTransport(error=PinnedHTTPTransportError("cloud_destination_blocked"))
        skill = WebSkill(allowed_hosts=("example.com",), transport=transport)
        with self.assertRaisesRegex(WebSkillError, "network firewall"):
            skill.execute("https://example.com/")

    def test_rejects_oversized_and_non_text_responses(self):
        too_large = WebSkill(
            allowed_hosts=("example.com",), max_bytes=4,
            transport=FakeTransport(FakeResponse(body=b"12345")),
        )
        with self.assertRaisesRegex(WebSkillError, "size limit"):
            too_large.execute("https://example.com/")
        binary = WebSkill(
            allowed_hosts=("example.com",),
            transport=FakeTransport(FakeResponse(content_type="application/octet-stream")),
        )
        with self.assertRaisesRegex(WebSkillError, "HTML or plain-text"):
            binary.execute("https://example.com/")


if __name__ == "__main__":
    unittest.main()
