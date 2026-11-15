"""DNS-pinned HTTPS transport for trusted cloud-provider requests.

The socket connects to the exact public IP validated by NetworkFirewall while
TLS certificate verification and SNI continue to use the original hostname.
Redirects are never followed.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit

from core.network_firewall import NetworkFirewall


class PinnedHTTPTransportError(RuntimeError):
    """Stable transport error that does not disclose secrets or endpoint data."""


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip, *, timeout, context):
        super().__init__(host, port=443, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        raw = socket.create_connection((self._ip, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class _Response:
    def __init__(self, response, connection):
        self._response = response
        self._connection = connection
        self.status = response.status
        self.reason = response.reason
        self.headers = response.headers

    def read(self, amount=-1):
        return self._response.read(amount)

    def readline(self, amount=-1):
        return self._response.readline(amount)

    def close(self):
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


class PinnedHTTPSProviderTransport:
    """Small urllib-Request-compatible HTTPS transport with a strict egress policy."""

    MAX_REQUEST_BYTES = 2_000_000
    _METHOD = re.compile(r"^[A-Z][A-Z0-9!#$%&'*+.^_`|~-]{0,15}$")
    _HEADER = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

    def __init__(self, *, allowed_hosts, timeout=45, resolver=None,
                 connection_factory=None, firewall_policy=None, ssl_context=None):
        if not isinstance(allowed_hosts, (set, frozenset, tuple, list)) or not allowed_hosts:
            raise ValueError("an explicit cloud host allow-list is required")
        hosts = set()
        for host in allowed_hosts:
            if not isinstance(host, str) or not host or host != host.strip():
                raise ValueError("invalid cloud host allow-list")
            normalized = host.lower().rstrip(".")
            try:
                ipaddress.ip_address(normalized)
            except ValueError:
                pass
            else:
                raise ValueError("IP literals are not valid cloud host rules")
            hosts.add(normalized)
        if len(hosts) != len(allowed_hosts):
            raise ValueError("duplicate cloud host rules")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be between 1 and 120 seconds")
        self.allowed_hosts = frozenset(hosts)
        self.timeout = float(timeout)
        self._resolver = resolver or socket.getaddrinfo
        self._factory = connection_factory or _PinnedHTTPSConnection
        self._ssl_context = ssl_context or ssl.create_default_context()
        if firewall_policy is None:
            firewall_policy = NetworkFirewall(allowed_hosts=self.allowed_hosts)
        if not isinstance(firewall_policy, NetworkFirewall):
            raise TypeError("firewall_policy must be a NetworkFirewall")
        if not self.allowed_hosts.issubset(firewall_policy.allowed_hosts):
            raise ValueError("firewall policy must allow every configured cloud host")
        if 443 not in firewall_policy.allowed_ports or "tcp" not in firewall_policy.allowed_protocols:
            raise ValueError("firewall policy must allow HTTPS over TCP port 443")
        self.firewall_policy = firewall_policy

    def _parse_url(self, url):
        if not isinstance(url, str) or not 1 <= len(url) <= 4096:
            raise PinnedHTTPTransportError("invalid_cloud_endpoint")
        if any(ord(char) <= 0x20 or ord(char) == 0x7f for char in url):
            raise PinnedHTTPTransportError("invalid_cloud_endpoint")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise PinnedHTTPTransportError("invalid_cloud_endpoint") from None
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme.lower() != "https" or not host or parts.username is not None
                or parts.password is not None or port not in (None, 443)
                or parts.fragment or host not in self.allowed_hosts):
            raise PinnedHTTPTransportError("untrusted_cloud_endpoint")
        path = parts.path or "/"
        if not path.startswith("/") or "\\r" in path or "\\n" in path:
            raise PinnedHTTPTransportError("invalid_cloud_endpoint")
        return host, path + (("?" + parts.query) if parts.query else "")

    def _resolve_and_authorize(self, host):
        try:
            records = self._resolver(host, 443, type=socket.SOCK_STREAM)
        except (OSError, TypeError):
            raise PinnedHTTPTransportError("cloud_resolution_failed") from None
        addresses = set()
        try:
            for record in records:
                ip = ipaddress.ip_address(record[4][0])
                if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                    ip = ip.ipv4_mapped
                if not ip.is_global:
                    raise PinnedHTTPTransportError("cloud_destination_blocked")
                addresses.add(str(ip))
        except (ValueError, IndexError, TypeError):
            raise PinnedHTTPTransportError("cloud_resolution_failed") from None
        if not addresses or len(addresses) > 32:
            raise PinnedHTTPTransportError("cloud_resolution_failed")
        decision = self.firewall_policy.authorize(
            host=host, port=443, protocol="tcp", resolved_addresses=tuple(addresses),
        )
        if not decision.allowed:
            raise PinnedHTTPTransportError("cloud_destination_blocked")
        return sorted(addresses)[0]

    @classmethod
    def _request_headers(cls, request):
        result = {}
        reserved = {"host", "connection", "content-length", "transfer-encoding"}
        for key, value in request.header_items():
            if (not isinstance(key, str) or not cls._HEADER.fullmatch(key)
                    or not isinstance(value, str)
                    or any(ord(char) < 0x20 or ord(char) == 0x7f for char in value)):
                raise PinnedHTTPTransportError("invalid_cloud_headers")
            if key.lower() in reserved or key.lower() in {name.lower() for name in result}:
                raise PinnedHTTPTransportError("invalid_cloud_headers")
            result[key] = value
        return result

    def open(self, request, timeout=None):
        if timeout is None:
            timeout = self.timeout
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise PinnedHTTPTransportError("invalid_cloud_timeout")
        try:
            host, target = self._parse_url(request.full_url)
            method = request.get_method()
            if not isinstance(method, str) or not self._METHOD.fullmatch(method):
                raise PinnedHTTPTransportError("invalid_cloud_method")
            body = request.data
            if body is not None and not isinstance(body, bytes):
                raise PinnedHTTPTransportError("invalid_cloud_body")
            if body is not None and len(body) > self.MAX_REQUEST_BYTES:
                raise PinnedHTTPTransportError("cloud_request_too_large")
            headers = self._request_headers(request)
            pinned_ip = self._resolve_and_authorize(host)
            connection = self._factory(host, pinned_ip, timeout=float(timeout), context=self._ssl_context)
            try:
                connection.request(method, target, body=body, headers=headers)
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    raise PinnedHTTPTransportError("cloud_redirect_rejected")
                return _Response(response, connection)
            except Exception:
                connection.close()
                raise
        except PinnedHTTPTransportError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError, ValueError):
            raise PinnedHTTPTransportError("cloud_endpoint_unavailable") from None
