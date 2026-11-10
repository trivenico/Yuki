import unittest

from core.mcp_pinned_transport import (
    MCPPinnedTransportError,
    PinnedMCPHTTPTransport,
)


def record(address):
    return (2, 1, 6, "", (address, 443))


class Response:
    status = 200
    def __init__(self, headers=None, body=b'{"jsonrpc":"2.0","id":1,"result":{}}'):
        self._headers = headers or [("Content-Type", "application/json")]
        self._body = body
    def getheaders(self):
        return self._headers
    def read(self, limit):
        return self._body[:limit]


class Connection:
    def __init__(self, *args, response=None, **kwargs):
        self.response = response or Response()
        self.request_args = None
    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)
    def getresponse(self):
        return self.response
    def close(self):
        pass


class PinnedMCPHTTPTransportTests(unittest.TestCase):
    def setUp(self):
        self.connections = []
        def factory(*args, **kwargs):
            value = Connection(*args, **kwargs)
            self.connections.append((args, kwargs, value))
            return value
        self.transport = PinnedMCPHTTPTransport(
            allowed_hosts={"mcp.example.com"},
            resolver=lambda *args, **kwargs: [record("8.8.8.8")],
            connection_factory=factory,
        )

    def test_connects_to_resolved_ip_and_preserves_host_for_tls(self):
        status, headers, body = self.transport.post_json(
            "https://mcp.example.com/rpc", payload={"jsonrpc":"2.0"},
            headers={"Authorization":"Bearer token"},
        )
        args, kwargs, connection = self.connections[0]
        self.assertEqual(args[:2], ("mcp.example.com", "8.8.8.8"))
        self.assertEqual(connection.request_args[0][0], "POST")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertIn(b"jsonrpc", body)

    def test_rejects_private_and_ipv4_mapped_private_destinations(self):
        for address in ("127.0.0.1", "::ffff:127.0.0.1"):
            transport = PinnedMCPHTTPTransport(
                allowed_hosts={"mcp.example.com"},
                resolver=lambda *args, **kwargs: [record(address)],
                connection_factory=lambda *args, **kwargs: self.fail("must not connect"),
            )
            with self.subTest(address=address), self.assertRaises(MCPPinnedTransportError):
                transport.post_json("https://mcp.example.com/rpc", payload={}, headers={})

    def test_rejects_redirects(self):
        class Redirect(Response):
            status = 302
        self.transport._factory = lambda *args, **kwargs: Connection(response=Redirect())
        with self.assertRaisesRegex(MCPPinnedTransportError, "mcp_redirect_rejected"):
            self.transport.post_json("https://mcp.example.com/rpc", payload={}, headers={})

    def test_rejects_header_override_and_untrusted_hosts(self):
        with self.assertRaises(MCPPinnedTransportError):
            self.transport.post_json(
                "https://mcp.example.com/rpc", payload={}, headers={"Host":"attacker.example"}
            )
        with self.assertRaises(MCPPinnedTransportError):
            self.transport.post_json("https://127.0.0.1/rpc", payload={}, headers={})

    def test_bounds_response(self):
        self.transport.MAX_RESPONSE_BYTES = 4
        with self.assertRaisesRegex(MCPPinnedTransportError, "mcp_response_too_large"):
            self.transport.post_json("https://mcp.example.com/rpc", payload={}, headers={})


if __name__ == "__main__":
    unittest.main()
