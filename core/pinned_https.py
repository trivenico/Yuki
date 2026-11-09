"""Pinned HTTPS transport primitives for trusted outbound integrations."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.request


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated IP while retaining hostname-based TLS verification."""

    def __init__(self, host, *, pinned_ip, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, context=None):
        super().__init__(host, timeout=timeout, context=context or ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self):
        raw_socket = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_ip):
        super().__init__(context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def https_open(self, request):
        return self.do_open(
            lambda host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, context=None,
                   check_hostname=None: PinnedHTTPSConnection(
                       host, pinned_ip=self._pinned_ip, timeout=timeout, context=context
                   ),
            request,
            context=self._context,
            check_hostname=self._check_hostname,
        )


def resolve_public_https_host(host: str, port: int = 443) -> tuple[str, ...]:
    """Resolve once, reject mixed/private answers, and return deterministic IPs."""
    try:
        addresses = {
            record[4][0]
            for record in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
        parsed = {address: ipaddress.ip_address(address) for address in addresses}
    except (OSError, ValueError, IndexError, TypeError) as exc:
        raise ValueError("HTTPS host could not be safely resolved.") from exc
    if not parsed or any(not address.is_global for address in parsed.values()):
        raise ValueError("Non-public HTTPS destinations are blocked.")
    return tuple(sorted(parsed, key=lambda value: (parsed[value].version, value)))


def build_pinned_https_opener(pinned_ip: str, *handlers):
    """Build a no-proxy opener whose TCP destination cannot be re-resolved."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        *handlers,
        PinnedHTTPSHandler(pinned_ip),
    )
