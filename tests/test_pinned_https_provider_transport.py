import unittest
from core.pinned_https_provider_transport import (
    PinnedHTTPSProviderTransport,
    PinnedHTTPTransportError,
)
from core.network_firewall import NetworkFirewall


def dns_record(address):
    return (2, 1, 6, "", (address, 443))


class FakeResponse:
    def __init__(self, status=200, body=b"ok"):
        self.status = status
        self.reason = "OK"
        self.headers = {"content-type": "application/json"}
        self.body = body

    def read(self, amount=-1):
        return self.body if amount < 0 else self.body[:amount]

    def readline(self, amount=-1):
        return self.read(amount)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, host, ip, *, timeout, context, status=200):
        self.host, self.ip = host, ip
        self.timeout, self.context = timeout, context
        self.status = status
        self.closed = False
        self.request_args = None

    def request(self, method, target, *, body, headers):
        self.request_args = (method, target, body, headers)

    def getresponse(self):
        return FakeResponse(self.status)

    def close(self):
        self.closed = True


class PinnedHTTPSProviderTransportTests(unittest.TestCase):
    def setUp(self):
        self.connections = []
        self.resolutions = []

        def resolver(host, port, *, type):
            self.resolutions.append((host, port, type))
            return [dns_record("8.8.8.8")]

        def factory(host, ip, **kwargs):
            connection = FakeConnection(host, ip, **kwargs)
            self.connections.append(connection)
            return connection

        self.transport = PinnedHTTPSProviderTransport(
            allowed_hosts={"api.openai.com"},
            resolver=resolver,
            connection_factory=factory,
        )

    def request(self, url="https://api.openai.com/v1/chat/completions"):
        from urllib.request import Request
        return Request(url, data=b'{"hello":"world"}',
                       headers={"Content-Type": "application/json",
                                "Authorization": "Bearer test"},
                       method="POST")

    def test_connects_to_validated_ip_and_preserves_hostname(self):
        with self.transport.open(self.request()) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"ok")
        connection = self.connections[0]
        self.assertEqual((connection.host, connection.ip), ("api.openai.com", "8.8.8.8"))
        self.assertEqual(connection.request_args[0:3],
                         ("POST", "/v1/chat/completions", b'{"hello":"world"}'))
        self.assertTrue(connection.closed)

    def test_rejects_private_or_mixed_dns_before_connecting(self):
        for addresses in (["127.0.0.1"], ["8.8.8.8", "10.0.0.1"]):
            with self.subTest(addresses=addresses):
                self.transport._resolver = lambda *args, values=addresses, **kwargs: [
                    dns_record(value) for value in values
                ]
                with self.assertRaises(PinnedHTTPTransportError):
                    self.transport.open(self.request())
        self.assertEqual(self.connections, [])

    def test_rejects_untrusted_host_without_dns_lookup(self):
        with self.assertRaises(PinnedHTTPTransportError):
            self.transport.open(self.request("https://attacker.example/steal"))
        self.assertEqual(self.resolutions, [])

    def test_rejects_redirect_and_closes_connection(self):
        redirected = []

        def factory(host, ip, **kwargs):
            connection = FakeConnection(host, ip, status=302, **kwargs)
            redirected.append(connection)
            return connection

        self.transport._factory = factory
        with self.assertRaises(PinnedHTTPTransportError):
            self.transport.open(self.request())
        self.assertEqual(len(redirected), 1)
        self.assertTrue(redirected[0].closed)

    def test_custom_firewall_must_allow_exact_provider_host(self):
        with self.assertRaises(ValueError):
            PinnedHTTPSProviderTransport(
                allowed_hosts={"api.openai.com"},
                firewall_policy=NetworkFirewall(allowed_hosts={"example.com"}),
            )

    def test_rejects_non_https_and_nonstandard_port(self):
        for url in ("http://api.openai.com/v1", "https://api.openai.com:444/v1"):
            with self.subTest(url=url), self.assertRaises(PinnedHTTPTransportError):
                self.transport.open(self.request(url))
        self.assertEqual(self.resolutions, [])


if __name__ == "__main__":
    unittest.main()
