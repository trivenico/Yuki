import unittest
from unittest.mock import patch

from providers.openai_compatible import (
    _PinnedHTTPSConnection,
    _build_pinned_opener,
    _resolve_public_address,
)


def _answer(address):
    return (2, 1, 6, "", (address, 443))


class PinnedProviderTransportTests(unittest.TestCase):
    @patch("providers.openai_compatible.socket.getaddrinfo")
    def test_resolves_and_pins_a_public_address(self, resolver):
        resolver.return_value = [_answer("8.8.8.8"), _answer("1.1.1.1")]
        self.assertEqual(_resolve_public_address("gateway.example", 443), "1.1.1.1")
        resolver.assert_called_once_with("gateway.example", 443, type=1)

    @patch("providers.openai_compatible.socket.getaddrinfo")
    def test_rejects_private_or_mixed_dns_answers(self, resolver):
        resolver.return_value = [_answer("8.8.8.8"), _answer("127.0.0.1")]
        with self.assertRaisesRegex(ValueError, "non-public"):
            _resolve_public_address("gateway.example", 443)

    @patch("providers.openai_compatible.socket.getaddrinfo")
    def test_rejects_empty_dns_answers(self, resolver):
        resolver.return_value = []
        with self.assertRaisesRegex(ValueError, "no usable public"):
            _resolve_public_address("gateway.example", 443)

    @patch("providers.openai_compatible.socket.create_connection")
    def test_connection_uses_pinned_ip_not_hostname(self, create_connection):
        class FakeTLSContext:
            def __init__(self):
                self.server_hostname = None
            def wrap_socket(self, raw, *, server_hostname):
                self.server_hostname = server_hostname
                return raw

        connection = _PinnedHTTPSConnection("gateway.example", "8.8.8.8", timeout=5)
        context = FakeTLSContext()
        connection._context = context
        raw = object()
        create_connection.return_value = raw
        connection.connect()
        create_connection.assert_called_once_with(("8.8.8.8", 443), 5, None)
        self.assertEqual(context.server_hostname, "gateway.example")
        self.assertIs(connection.sock, raw)

    @patch("providers.openai_compatible.urllib.request.build_opener")
    @patch("providers.openai_compatible.socket.getaddrinfo")
    def test_opener_disables_ambient_proxy_and_keeps_redirect_rejection(self, resolver, build_opener):
        resolver.return_value = [_answer("8.8.8.8")]
        _build_pinned_opener("https://gateway.example/v1")
        handlers = build_opener.call_args.args
        self.assertEqual(len(handlers), 3)
        self.assertEqual(handlers[0].proxies, {})
        self.assertEqual(handlers[1]._pinned_ip, "8.8.8.8")
        self.assertEqual(handlers[2].__class__.__name__, "_RejectRedirects")


if __name__ == "__main__":
    unittest.main()
