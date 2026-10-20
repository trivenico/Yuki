"""Offline regression tests for URL validation and bounded web fetching."""

import socket
import unittest
import urllib.error
from unittest.mock import Mock, patch

from skills.web_skill import WebSkill, WebSkillError


class WebSkillSecurityTests(unittest.TestCase):
    def test_rejects_invalid_url_forms_before_network_access(self):
        skill = WebSkill(allowed_hosts=("example.com",))
        for url in (
            "", "http://example.com", "https://user@example.com", "https://example.com:8443",
            "https://localhost", "https://service.local", "https://example.com" + "x" * 2050,
        ):
            with self.subTest(url=url[:40]), self.assertRaises(WebSkillError):
                skill.execute(url)

    def test_requires_explicit_allowlist(self):
        with self.assertRaisesRegex(WebSkillError, "allow-list"):
            WebSkill().execute("https://example.com")

    @patch("skills.web_skill.socket.getaddrinfo")
    def test_rejects_private_dns_answers(self, getaddrinfo):
        getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with self.assertRaisesRegex(WebSkillError, "non-public"):
            WebSkill(allowed_hosts=("example.com",)).execute("https://example.com")
        getaddrinfo.assert_called_once()

    @patch("skills.web_skill.socket.getaddrinfo", side_effect=OSError("DNS down"))
    def test_dns_failure_is_sanitized(self, _getaddrinfo):
        with self.assertRaisesRegex(WebSkillError, "could not be resolved"):
            WebSkill(allowed_hosts=("example.com",)).execute("https://example.com")

    @patch("skills.web_skill.socket.getaddrinfo")
    @patch("skills.web_skill.urllib.request.build_opener")
    def test_rejects_non_text_content_type(self, build_opener, getaddrinfo):
        getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        response = Mock()
        response.headers = {"Content-Type": "application/octet-stream"}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        build_opener.return_value.open.return_value = response
        with self.assertRaisesRegex(WebSkillError, "Only HTML"):
            WebSkill(allowed_hosts=("example.com",)).execute("https://example.com")
        response.read.assert_not_called()

    @patch("skills.web_skill.socket.getaddrinfo")
    @patch("skills.web_skill.urllib.request.build_opener")
    def test_rejects_oversized_page(self, build_opener, getaddrinfo):
        getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        response = Mock()
        response.headers = {"Content-Type": "text/plain; charset=utf-8"}
        response.read.return_value = b"x" * 11
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        build_opener.return_value.open.return_value = response
        with self.assertRaisesRegex(WebSkillError, "size limit"):
            WebSkill(allowed_hosts=("example.com",), max_bytes=10).execute("https://example.com")

    @patch("skills.web_skill.socket.getaddrinfo")
    @patch("skills.web_skill.urllib.request.build_opener")
    def test_extracts_text_and_returns_bounded_fields(self, build_opener, getaddrinfo):
        getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        response = Mock()
        response.headers = {"Content-Type": "text/html"}
        response.read.return_value = b"<html><title>Example</title><body>Hello &amp; welcome</body></html>"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        build_opener.return_value.open.return_value = response
        result = WebSkill(allowed_hosts=("example.com",)).execute("https://example.com")
        self.assertEqual(result["url"], "https://example.com")
        self.assertIn("Hello & welcome", result["text"])
        self.assertLessEqual(len(result["text"]), 12000)
        build_opener.return_value.open.assert_called_once()
        self.assertEqual(build_opener.return_value.open.call_args.kwargs["timeout"], 8)

    @patch("skills.web_skill.socket.getaddrinfo")
    @patch("skills.web_skill.urllib.request.build_opener")
    def test_redirects_are_not_followed(self, build_opener, getaddrinfo):
        getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        build_opener.return_value.open.side_effect = urllib.error.HTTPError(
            "https://example.com", 302, "redirect", {}, None
        )
        with self.assertRaisesRegex(WebSkillError, "redirects are not followed"):
            WebSkill(allowed_hosts=("example.com",)).execute("https://example.com")


if __name__ == "__main__":
    unittest.main()
