import unittest
from unittest.mock import patch

from core.mcp_client import MCPError, MCPHTTPClient


class MCPClientEndpointTests(unittest.TestCase):
    def test_rejects_malformed_port_as_mcp_error(self):
        for endpoint in (
            "https://mcp.example.com:bad/api",
            "https://mcp.example.com:65536/api",
            "https://[invalid-ipv6/api",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(MCPError):
                    MCPHTTPClient._validate_endpoint(endpoint, {"mcp.example.com"})

    @patch("core.mcp_client.socket.getaddrinfo")
    def test_accepts_allowlisted_https_public_endpoint(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        endpoint = "https://mcp.example.com/api"
        self.assertEqual(
            MCPHTTPClient._validate_endpoint(endpoint, {"mcp.example.com"}),
            endpoint,
        )

    @patch("core.mcp_client.socket.getaddrinfo")
    def test_rejects_endpoint_resolving_to_non_public_address(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaisesRegex(MCPError, "Non-public"):
            MCPHTTPClient._validate_endpoint(
                "https://mcp.example.com/api", {"mcp.example.com"}
            )

    def test_requires_exact_allowlisted_host_and_https(self):
        with self.assertRaisesRegex(MCPError, "allow-list"):
            MCPHTTPClient._validate_endpoint(
                "https://evil.example/api", {"mcp.example.com"}
            )
        with self.assertRaisesRegex(MCPError, "HTTPS"):
            MCPHTTPClient._validate_endpoint(
                "http://mcp.example.com/api", {"mcp.example.com"}
            )


if __name__ == "__main__":
    unittest.main()
