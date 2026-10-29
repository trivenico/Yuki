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


    @patch("core.mcp_client.socket.getaddrinfo")
    @patch("core.mcp_client._PinnedHTTPSConnection")
    def test_rpc_uses_pinned_public_ip_and_does_not_follow_redirects(self, connection_cls, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        connection = connection_cls.return_value
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: (
            "application/json" if name == "Content-Type" else default
        )
        response.read.return_value = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'

        client = MCPHTTPClient(
            "https://mcp.example.com/rpc?mode=read",
            allowed_hosts={"mcp.example.com"},
            timeout=2,
        )
        result = client._rpc("tools/list", {})

        self.assertEqual(client._pinned_address, "93.184.216.34")
        connection_cls.assert_called_once_with(
            "mcp.example.com", "93.184.216.34", timeout=2
        )
        connection.request.assert_called_once()
        self.assertEqual(connection.request.call_args.args[1], "/rpc?mode=read")
        self.assertEqual(result, {"ok": True})
        connection.close.assert_called_once()

    @patch("core.mcp_client.socket.getaddrinfo")
    def test_constructor_rejects_private_answer_from_transport_resolution(self, getaddrinfo):
        getaddrinfo.side_effect = [
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("127.0.0.1", 443))],
        ]
        with self.assertRaisesRegex(MCPError, "Non-public"):
            MCPHTTPClient("https://mcp.example.com/api", allowed_hosts={"mcp.example.com"})

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
