import json
import socket
import unittest

from core.oauth_http_transport import OAuthHTTPError, PinnedOAuthHTTPTransport


def record(address):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))


class FakeResponse:
    def __init__(self, status=200, body=b'{"ok":true}', content_type="application/json"):
        self.status, self.body, self.content_type = status, body, content_type
    def getheader(self, name, default=None):
        return self.content_type if name.lower() == "content-type" else default
    def read(self, amount=-1):
        return self.body[:amount]


class FakeConnection:
    def __init__(self, host, pinned_ip, *, port, timeout, context):
        self.host, self.pinned_ip, self.port = host, pinned_ip, port
        self.timeout, self.context = timeout, context
        self.closed = False
        self.request_args = None
        self.response = FakeResponse()
    def request(self, method, target, body=None, headers=None):
        self.request_args = (method, target, body, headers)
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


class PinnedOAuthHTTPTransportTests(unittest.TestCase):
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

    def test_pins_resolved_public_ip_and_preserves_host_for_tls(self):
        result = self.transport.request_json("GET", "https://oauth.example.com/.well-known/openid-configuration")
        connection = self.connections[0]
        self.assertEqual(result, {"ok": True})
        self.assertEqual(connection.host, "oauth.example.com")
        self.assertEqual(connection.pinned_ip, "93.184.216.34")
        self.assertEqual(connection.request_args[1], "/.well-known/openid-configuration")
        self.assertTrue(connection.closed)

    def test_posts_bounded_json_and_authorization_header(self):
        self.transport.request_json(
            "POST", "https://oauth.example.com/token",
            payload={"grant_type": "authorization_code", "code": "secret"},
            headers={"Authorization": "Basic client"},
        )
        method, target, body, headers = self.connections[0].request_args
        self.assertEqual(method, "POST")
        self.assertEqual(target, "/token")
        self.assertEqual(json.loads(body), {"grant_type": "authorization_code", "code": "secret"})
        self.assertEqual(headers["Authorization"], "Basic client")

    def test_rejects_unlisted_hosts_and_unsafe_endpoint_urls(self):
        for endpoint in (
            "http://oauth.example.com/token",
            "https://attacker.example/token",
            "https://user@oauth.example.com/token",
            "https://oauth.example.com:444/token",
            "https://oauth.example.com/token#fragment",
            "https://oauth.example.com/token\r\nHost: attacker",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(OAuthHTTPError):
                self.transport.request_json("GET", endpoint)
        self.assertEqual(self.connections, [])

    def test_rejects_private_and_mixed_dns_answers(self):
        for addresses in (
            ["127.0.0.1"],
            ["93.184.216.34", "10.0.0.1"],
            ["169.254.169.254"],
            ["::1"],
        ):
            transport = PinnedOAuthHTTPTransport(
                allowed_hosts={"oauth.example.com"},
                resolver=lambda *args, addresses=addresses, **kwargs: [record(a) for a in addresses],
                connection_factory=lambda *args, **kwargs: self.fail("must not connect"),
            )
            with self.subTest(addresses=addresses), self.assertRaises(OAuthHTTPError):
                transport.request_json("GET", "https://oauth.example.com/metadata")

    def test_rejects_empty_or_failed_dns(self):
        for resolver in (lambda *a, **k: [], lambda *a, **k: (_ for _ in ()).throw(OSError())):
            transport = PinnedOAuthHTTPTransport(
                allowed_hosts={"oauth.example.com"}, resolver=resolver
            )
            with self.assertRaises(OAuthHTTPError):
                transport.request_json("GET", "https://oauth.example.com/metadata")

    def test_rejects_redirects_without_following(self):
        self.connections_factory_response(status=302)
        with self.assertRaisesRegex(OAuthHTTPError, "oauth_redirect_rejected"):
            self.transport.request_json("GET", "https://oauth.example.com/metadata")
        self.assertTrue(self.connections[-1].closed)

    def test_rejects_non_json_invalid_json_and_non_object_response(self):
        cases = (
            FakeResponse(content_type="text/html"),
            FakeResponse(body=b"not-json"),
            FakeResponse(body=b"[]"),
        )
        for response in cases:
            self.connections_factory_response(response=response)
            with self.subTest(response=response), self.assertRaises(OAuthHTTPError):
                self.transport.request_json("GET", "https://oauth.example.com/metadata")

    def test_rejects_oversized_response_and_request(self):
        self.connections_factory_response(
            response=FakeResponse(body=b'{"x":"' + b"a" * (1_000_001) + b'"}')
        )
        with self.assertRaisesRegex(OAuthHTTPError, "oauth_response_too_large"):
            self.transport.request_json("GET", "https://oauth.example.com/metadata")
        with self.assertRaisesRegex(OAuthHTTPError, "oauth_request_too_large"):
            self.transport.request_json(
                "POST", "https://oauth.example.com/token", payload={"data": "x" * 65000}
            )

    def test_rejects_unsafe_custom_headers_and_post_without_body(self):
        for headers in (
            {"Host": "attacker.example"},
            {"Content-Length": "0"},
            {"Content-Type": "text/plain"},
            {"X-Test": "safe\\r\\nInjected: yes"},
            {"Bad Header": "value"},
            {"X-Test": "one", "x-test": "two"},
        ):
            with self.subTest(headers=headers), self.assertRaises(OAuthHTTPError):
                self.transport.request_json("GET", "https://oauth.example.com/metadata", headers=headers)
        with self.assertRaises(OAuthHTTPError):
            self.transport.request_json("POST", "https://oauth.example.com/token")

    def connections_factory_response(self, *, status=200, response=None):
        original = self.transport._connection_factory
        def factory(*args, **kwargs):
            connection = original(*args, **kwargs)
            if response is not None:
                connection.response = response
            else:
                connection.response = FakeResponse(status=status)
            return connection
        self.transport._connection_factory = factory


if __name__ == "__main__":
    unittest.main()
