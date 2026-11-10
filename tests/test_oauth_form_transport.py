import unittest
from urllib.parse import parse_qs

from core.oauth_http_transport import OAuthHTTPError, PinnedOAuthHTTPTransport
import socket

def record(address):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))

class FakeResponse:
    status = 200
    def getheader(self, name, default=None):
        return "application/json" if name.lower() == "content-type" else default
    def read(self, amount=-1):
        return b'{"ok":true}'[:amount]


class FakeConnection:
    def __init__(self, host, pinned_ip, *, port, timeout, context):
        self.host, self.pinned_ip = host, pinned_ip
        self.response = FakeResponse()
        self.request_args = None
        self.closed = False
    def request(self, method, target, body=None, headers=None):
        self.request_args = (method, target, body, headers)
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


class OAuthFormTransportTests(unittest.TestCase):
    def setUp(self):
        self.connections = []
        def factory(*args, **kwargs):
            connection = FakeConnection(*args, **kwargs)
            self.connections.append(connection)
            return connection
        self.transport = PinnedOAuthHTTPTransport(
            allowed_hosts={"oauth.example.com"},
            resolver=lambda host, port, type: [record("93.184.216.34")],
            connection_factory=factory,
        )

    def test_form_encoded_authorization_code_exchange(self):
        result = self.transport.request_form(
            "https://oauth.example.com/token",
            fields={
                "grant_type": "authorization_code",
                "code": "one-time-code",
                "redirect_uri": "https://yuki.example/callback",
                "code_verifier": "pkce-verifier",
                "client_id": "public-client",
            },
            headers={"Authorization": "Basic client-auth"},
        )
        method, target, body, headers = self.connections[0].request_args
        self.assertEqual(result, {"ok": True})
        self.assertEqual(method, "POST")
        self.assertEqual(target, "/token")
        self.assertEqual(headers["Content-Type"], "application/x-www-form-urlencoded")
        self.assertEqual(headers["Authorization"], "Basic client-auth")
        self.assertEqual(parse_qs(body.decode()), {
            "grant_type": ["authorization_code"],
            "code": ["one-time-code"],
            "redirect_uri": ["https://yuki.example/callback"],
            "code_verifier": ["pkce-verifier"],
            "client_id": ["public-client"],
        })
        self.assertTrue(self.connections[0].closed)

    def test_form_validation_and_header_override_rejected(self):
        for fields in ({}, {"code": object()}, {"code": "bad\\nvalue"}, {"x": "x" * 65000}):
            with self.subTest(fields=fields), self.assertRaises(OAuthHTTPError):
                self.transport.request_form("https://oauth.example.com/token", fields=fields)
        with self.assertRaises(OAuthHTTPError):
            self.transport.request_form(
                "https://oauth.example.com/token", fields={"code": "x"},
                headers={"Content-Type": "application/json"},
            )


if __name__ == "__main__":
    unittest.main()
