import unittest
from unittest.mock import MagicMock, patch

from core.mcp_client import MCPError, MCPHTTPClient, _PinnedHTTPSConnection


class MCPNetworkPinningTests(unittest.TestCase):
    def test_endpoint_resolution_is_returned_for_connection_pinning(self):
        records = [
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ]
        with patch("core.mcp_client.socket.getaddrinfo", return_value=records):
            endpoint, addresses = MCPHTTPClient._validate_endpoint(
                "https://mcp.example.com/mcp", ["mcp.example.com"]
            )
        self.assertEqual(endpoint, "https://mcp.example.com/mcp")
        self.assertEqual(set(addresses), {"8.8.8.8", "1.1.1.1"})

    def test_endpoint_rejects_mixed_public_and_private_dns_answers(self):
        records = [
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with patch("core.mcp_client.socket.getaddrinfo", return_value=records):
            with self.assertRaisesRegex(MCPError, "Non-public"):
                MCPHTTPClient._validate_endpoint(
                    "https://mcp.example.com/mcp", ["mcp.example.com"]
                )

    def test_client_rejects_unbounded_timeout_and_malformed_allowlists(self):
        records = [(2, 1, 6, "", ("8.8.8.8", 443))]
        for timeout in (0, -1, 61, True, float("nan"), float("inf")):
            with self.subTest(timeout=timeout):
                with patch("core.mcp_client.socket.getaddrinfo", return_value=records):
                    with self.assertRaisesRegex(ValueError, "timeout"):
                        MCPHTTPClient(
                            "https://mcp.example.com/mcp",
                            allowed_hosts=["mcp.example.com"],
                            timeout=timeout,
                        )
        with patch("core.mcp_client.socket.getaddrinfo", return_value=records):
            with self.assertRaisesRegex(ValueError, "allowed_tools"):
                MCPHTTPClient(
                    "https://mcp.example.com/mcp",
                    allowed_hosts=["mcp.example.com"],
                    allowed_tools=[{"not": "hashable"}],
                )

    def test_connection_uses_pinned_ip_but_tls_hostname(self):
        raw_socket = MagicMock()
        tls_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = tls_socket
        with patch("core.mcp_client.socket.create_connection", return_value=raw_socket) as connect:
            connection = _PinnedHTTPSConnection(
                "mcp.example.com", pinned_ip="8.8.8.8", context=context
            )
            connection.connect()
        self.assertEqual(connect.call_args.args[0], ("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="mcp.example.com")
        self.assertIs(connection.sock, tls_socket)

    def test_client_disables_environment_proxy_handlers(self):
        records = [(2, 1, 6, "", ("8.8.8.8", 443))]
        with patch("core.mcp_client.socket.getaddrinfo", return_value=records):
            client = MCPHTTPClient(
                "https://mcp.example.com/mcp", allowed_hosts=["mcp.example.com"]
            )
        proxy_handlers = [h for h in client._opener.handlers if h.__class__.__name__ == "ProxyHandler"]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})


if __name__ == "__main__":
    unittest.main()
