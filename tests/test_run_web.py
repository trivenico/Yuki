"""Private web launcher bind-policy tests."""
from __future__ import annotations

import unittest

from scripts.run_web import validate_bind_host


class WebBindPolicyTests(unittest.TestCase):
    def test_loopback_hosts_are_allowed(self):
        for host in ("127.0.0.1", "localhost", "::1", " localhost "):
            with self.subTest(host=host):
                self.assertEqual(validate_bind_host(host), host.strip())

    def test_wildcard_and_non_loopback_hosts_are_rejected(self):
        for host in ("0.0.0.0", "::", "192.0.2.10", "example.com", ""):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    validate_bind_host(host)

    def test_non_string_host_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_bind_host(None)


if __name__ == "__main__":
    unittest.main()
