import unittest
from core.pinned_https_provider_transport import (
    PinnedHTTPSProviderTransport,
    PinnedHTTPTransportError,
)
from core.network_firewall import NetworkFirewall


def dns_record(address):
    return (2, 1, 6, "", (address, 443))


class FakeResponse:
    def __init__(self, status=200, body=b"ok", on_read=None):
        self.status = status
        self.reason = "OK"
        self.headers = {"content-type": "application/json"}
        self.body = body
        self.on_read = on_read
        self.closed = False

    def read(self, amount=-1):
        if self.on_read is not None:
            self.on_read()
        return self.body if amount < 0 else self.body[:amount]

    def readline(self, amount=-1):
        return self.read(amount)

    def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self):
        self.timeouts = []
    def settimeout(self, value):
        self.timeouts.append(value)


class FakeConnection:
    def __init__(self, host, ip, *, timeout, context, status=200):
        self.host, self.ip = host, ip
        self.timeout, self.context = timeout, context
        self.status = status
        self.sock = FakeSocket()
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

    def test_response_reads_fail_closed_after_total_deadline(self):
        now = [10.0]
        self.transport._clock = lambda: now[0]
        with self.transport.open(self.request()) as response:
            now[0] = 56.0
            with self.assertRaises(TimeoutError):
                response.read(1)

    def test_remaining_deadline_is_applied_to_socket_before_response_read(self):
        now = [10.0]
        self.transport._clock = lambda: now[0]
        with self.transport.open(self.request()) as response:
            now[0] = 20.0
            response.read(1)
        self.assertEqual(self.connections[0].sock.timeouts[-1], 35.0)

    def test_read_crossing_total_deadline_discards_data_and_closes_connection(self):
        now = [10.0]
        responses = []

        def factory(host, ip, **kwargs):
            connection = FakeConnection(host, ip, **kwargs)
            response = FakeResponse(on_read=lambda: now.__setitem__(0, 56.0))
            connection.getresponse = lambda: response
            responses.append(response)
            self.connections.append(connection)
            return connection

        self.transport._factory = factory
        self.transport._clock = lambda: now[0]
        response = self.transport.open(self.request())
        with self.assertRaises(TimeoutError):
            response.read(1)
        self.assertTrue(responses[0].closed)
        self.assertTrue(self.connections[0].closed)

    def test_readline_crossing_total_deadline_discards_data_and_closes_connection(self):
        now = [10.0]
        responses = []

        def factory(host, ip, **kwargs):
            connection = FakeConnection(host, ip, **kwargs)
            response = FakeResponse(on_read=lambda: now.__setitem__(0, 56.0))
            connection.getresponse = lambda: response
            responses.append(response)
            self.connections.append(connection)
            return connection

        self.transport._factory = factory
        self.transport._clock = lambda: now[0]
        response = self.transport.open(self.request())
        with self.assertRaises(TimeoutError):
            response.readline(128)
        self.assertTrue(responses[0].closed)
        self.assertTrue(self.connections[0].closed)

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

    def test_rejects_multiple_trailing_dots_before_dns_or_connect(self):
        with self.assertRaisesRegex(PinnedHTTPTransportError, "invalid_cloud_endpoint"):
            self.transport.open(self.request("https://api.openai.com../v1"))
        self.assertEqual(self.resolutions, [])

    def test_rejects_non_https_and_nonstandard_port(self):
        for url in ("http://api.openai.com/v1", "https://api.openai.com:444/v1"):
            with self.subTest(url=url), self.assertRaises(PinnedHTTPTransportError):
                self.transport.open(self.request(url))
        self.assertEqual(self.resolutions, [])


    def test_shared_firewall_audit_runs_before_provider_connection(self):
        decisions = []
        firewall = NetworkFirewall(
            allowed_hosts=("api.openai.com",),
            audit=decisions.append,
        )
        transport = PinnedHTTPSProviderTransport(
            allowed_hosts={"api.openai.com"},
            resolver=lambda *args, **kwargs: [dns_record("8.8.8.8")],
            connection_factory=lambda host, ip, **kwargs: self.connections.append(
                FakeConnection(host, ip, **kwargs)
            ) or self.connections[-1],
            firewall_policy=firewall,
        )

        with transport.open(self.request()) as response:
            self.assertEqual(response.status, 200)

        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0].allowed)
        self.assertEqual(decisions[0].reason, "allowed")
        self.assertEqual(len(self.connections), 1)

    def test_shared_firewall_audit_failure_prevents_provider_connection(self):
        firewall = NetworkFirewall(
            allowed_hosts=("api.openai.com",),
            audit=lambda decision: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
        )
        transport = PinnedHTTPSProviderTransport(
            allowed_hosts={"api.openai.com"},
            resolver=lambda *args, **kwargs: [dns_record("8.8.8.8")],
            connection_factory=lambda host, ip, **kwargs: self.connections.append(
                FakeConnection(host, ip, **kwargs)
            ) or self.connections[-1],
            firewall_policy=firewall,
        )

        with self.assertRaises(PinnedHTTPTransportError):
            transport.open(self.request())

        self.assertEqual(self.connections, [])


if __name__ == "__main__":
    unittest.main()
