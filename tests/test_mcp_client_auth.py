import json
import unittest
from unittest.mock import patch

from core.mcp_client import MCPError, MCPHTTPClient


class _Response:
    headers = {"Content-Type": "application/json"}

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self._body


class _Opener:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return _Response(json.dumps({"jsonrpc": "2.0", "result": self.result}).encode())


class MCPHTTPAuthorizationTests(unittest.TestCase):
    def _client(self, provider):
        with patch.object(MCPHTTPClient, "_validate_endpoint", return_value="https://mcp.example.com/mcp"):
            client = MCPHTTPClient(
                "https://mcp.example.com/mcp",
                allowed_hosts=["mcp.example.com"],
                access_token_provider=provider,
            )
        client._opener = _Opener({"ok": True})
        return client

    def test_injected_token_is_sent_as_bearer_header(self):
        client = self._client(lambda: "opaque.token-123")
        self.assertEqual(client._rpc("tools/list", {}), {"ok": True})
        self.assertEqual(client._opener.requests[0].get_header("Authorization"), "Bearer opaque.token-123")

    def test_token_provider_is_called_for_each_request(self):
        tokens = iter(["first-token", "second-token"])
        client = self._client(lambda: next(tokens))
        client._rpc("one")
        client._rpc("two")
        self.assertEqual(
            [r.get_header("Authorization") for r in client._opener.requests],
            ["Bearer first-token", "Bearer second-token"],
        )

    def test_absent_provider_keeps_public_mcp_behavior(self):
        client = self._client(None)
        client._rpc("tools/list", {})
        self.assertIsNone(client._opener.requests[0].get_header("Authorization"))

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
            self.assertEqual(client._opener.requests, [])

    def test_provider_must_be_callable(self):
        with patch.object(MCPHTTPClient, "_validate_endpoint", return_value="https://mcp.example.com/mcp"):
            with self.assertRaises(TypeError):
                MCPHTTPClient(
                    "https://mcp.example.com/mcp",
                    allowed_hosts=["mcp.example.com"],
                    access_token_provider="plaintext-token",
                )


if __name__ == "__main__":
    unittest.main()
