import json
import unittest
from unittest.mock import patch

from core.mcp_client import MCPError, MCPHTTPClient


class _Transport:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def post_json(self, endpoint, *, payload, headers):
        self.requests.append((endpoint, payload, headers))
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": self.result,
        }).encode()
        return 200, {"content-type": "application/json"}, body


class MCPHTTPAuthorizationTests(unittest.TestCase):
    def _client(self, provider):
        with patch.object(MCPHTTPClient, "_validate_endpoint",
                          return_value="https://mcp.example.com/mcp"):
            transport = _Transport({"ok": True})
            client = MCPHTTPClient(
                "https://mcp.example.com/mcp",
                allowed_hosts=["mcp.example.com"],
                access_token_provider=provider,
                http_transport=transport,
            )
        client._test_transport = transport
        return client

    def test_injected_token_is_sent_as_bearer_header(self):
        client = self._client(lambda: "opaque.token-123")
        self.assertEqual(client._rpc("tools/list", {}), {"ok": True})
        self.assertEqual(
            client._test_transport.requests[0][2]["Authorization"],
            "Bearer opaque.token-123",
        )

    def test_token_provider_is_called_for_each_request(self):
        tokens = iter(["first-token", "second-token"])
        client = self._client(lambda: next(tokens))
        client._rpc("one")
        client._rpc("two")
        self.assertEqual(
            [r[2]["Authorization"] for r in client._test_transport.requests],
            ["Bearer first-token", "Bearer second-token"],
        )

    def test_absent_provider_keeps_public_mcp_behavior(self):
        client = self._client(None)
        client._rpc("tools/list", {})
        self.assertNotIn("Authorization", client._test_transport.requests[0][2])

    def test_invalid_or_unavailable_credentials_fail_closed_without_leaking(self):
        for provider in (
            lambda: "token\r\nX-Injected: yes",
            lambda: "contains spaces",
            lambda: "",
            lambda: "x" * 8193,
            lambda: (_ for _ in ()).throw(RuntimeError("secret-token-value")),
        ):
            client = self._client(provider)
            with self.subTest(provider=provider), self.assertRaises(MCPError) as raised:
                client._rpc("tools/list", {})
            self.assertNotIn("secret-token-value", str(raised.exception))
            self.assertEqual(client._test_transport.requests, [])

    def test_provider_must_be_callable(self):
        with patch.object(MCPHTTPClient, "_validate_endpoint",
                          return_value="https://mcp.example.com/mcp"):
            with self.assertRaises(TypeError):
                MCPHTTPClient(
                    "https://mcp.example.com/mcp",
                    allowed_hosts=["mcp.example.com"],
                    access_token_provider="plaintext-token",
                )


if __name__ == "__main__":
    unittest.main()
