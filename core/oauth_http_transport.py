"""Pinned, redirect-free HTTPS JSON transport for trusted OAuth endpoints.

DNS is resolved once per request and the selected public IP is pinned to the
TCP connection while TLS SNI and certificate verification use the original host.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlencode, urlsplit


class OAuthHTTPError(RuntimeError):
    """Stable, non-sensitive OAuth transport failure."""


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, pinned_ip, *, port=443, timeout=10, context=None):
        super().__init__(hostname, port=port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self):
        raw = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class PinnedOAuthHTTPTransport:
    """Small JSON-only HTTPS client for explicitly reviewed OAuth hosts."""

    MAX_RESPONSE_BYTES = 1_000_000
    MAX_REQUEST_BYTES = 64_000

    def __init__(self, *, allowed_hosts, timeout=10, resolver=None, connection_factory=None):
        if not isinstance(allowed_hosts, (set, frozenset, tuple, list)) or not allowed_hosts:
            raise ValueError("an explicit OAuth host allow-list is required")
        normalized = set()
        for host in allowed_hosts:
            if not isinstance(host, str) or not host or host != host.strip():
                raise ValueError("invalid OAuth host allow-list")
            normalized.add(host.lower().rstrip("."))
        self.allowed_hosts = frozenset(normalized)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        self.timeout = float(timeout)
        self._resolver = resolver or socket.getaddrinfo
        self._connection_factory = connection_factory or _PinnedHTTPSConnection
        self._ssl_context = ssl.create_default_context()

    def _parse_endpoint(self, endpoint):
        if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 2048:
            raise OAuthHTTPError("invalid_oauth_endpoint")
        if any(ord(char) < 0x21 or ord(char) == 0x7f for char in endpoint):
            raise OAuthHTTPError("invalid_oauth_endpoint")
        try:
            parts = urlsplit(endpoint)
            port = parts.port
        except ValueError:
            raise OAuthHTTPError("invalid_oauth_endpoint") from None
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or port not in (None, 443)
                or parts.fragment or host not in self.allowed_hosts):
            raise OAuthHTTPError("untrusted_oauth_endpoint")
        path = parts.path or "/"
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise OAuthHTTPError("invalid_oauth_endpoint")
        target = path + (("?" + parts.query) if parts.query else "")
        return host, target

    def _resolve_public_ip(self, host):
        try:
            records = self._resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise OAuthHTTPError("oauth_endpoint_resolution_failed") from None
        addresses = set()
        for record in records:
            try:
                address = ipaddress.ip_address(record[4][0])
            except (ValueError, IndexError, TypeError):
                raise OAuthHTTPError("oauth_endpoint_resolution_failed") from None
            if not address.is_global:
                raise OAuthHTTPError("oauth_endpoint_address_blocked")
            addresses.add(str(address))
        if not addresses:
            raise OAuthHTTPError("oauth_endpoint_resolution_failed")
        # Deterministic choice avoids retrying alternate DNS answers on failure.
        return sorted(addresses)[0]

    @staticmethod
    def _custom_headers(headers, *, reserved):
        if headers is None:
            return {}
        if not isinstance(headers, dict):
            raise OAuthHTTPError("invalid_oauth_headers")
        result = {}
        seen = set()
        for key, value in headers.items():
            if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                    or not isinstance(value, str)
                    or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value)):
                raise OAuthHTTPError("invalid_oauth_headers")
            normalized = key.lower()
            if normalized in reserved or normalized in seen:
                raise OAuthHTTPError("invalid_oauth_headers")
            seen.add(normalized)
            result[key] = value
        return result

    def request_json(self, method, endpoint, *, payload=None, headers=None):
        if method not in {"GET", "POST"}:
            raise OAuthHTTPError("oauth_http_method_not_allowed")
        host, target = self._parse_endpoint(endpoint)
        pinned_ip = self._resolve_public_ip(host)
        body = None
        request_headers = {"Accept": "application/json", "User-Agent": "Yuki-OAuth/1.0"}
        custom_headers = self._custom_headers(
            headers,
            reserved={"host", "connection", "content-length", "transfer-encoding", "content-type"},
        )
        request_headers.update(custom_headers)
        if payload is not None:
            try:
                body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError):
                raise OAuthHTTPError("invalid_oauth_payload") from None
            if len(body) > self.MAX_REQUEST_BYTES:
                raise OAuthHTTPError("oauth_request_too_large")
            request_headers["Content-Type"] = "application/json"
        elif method == "POST":
            raise OAuthHTTPError("oauth_post_payload_required")

        connection = self._connection_factory(
            host, pinned_ip, port=443, timeout=self.timeout, context=self._ssl_context
        )
        try:
            connection.request(method, target, body=body, headers=request_headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise OAuthHTTPError("oauth_redirect_rejected")
            if response.status < 200 or response.status >= 300:
                raise OAuthHTTPError("oauth_http_request_failed")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise OAuthHTTPError("oauth_response_content_type_invalid")
            raw = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(raw) > self.MAX_RESPONSE_BYTES:
                raise OAuthHTTPError("oauth_response_too_large")
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise OAuthHTTPError("oauth_response_invalid_json") from None
            if not isinstance(value, dict):
                raise OAuthHTTPError("oauth_response_invalid_shape")
            return value
        except OAuthHTTPError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError):
            raise OAuthHTTPError("oauth_endpoint_unavailable") from None
        finally:
            connection.close()

    def request_form_status(self, endpoint, *, fields, headers=None):
        """POST a bounded form and return the HTTP status without parsing a body.

        OAuth revocation endpoints commonly return an empty 200 response. This
        deliberately separate primitive avoids weakening the JSON token path.
        """
        if not isinstance(fields, dict) or not fields:
            raise OAuthHTTPError("invalid_oauth_form")
        normalized = []
        for key, value in fields.items():
            if (not isinstance(key, str) or not key or len(key) > 128
                    or not isinstance(value, str) or len(value) > 8192
                    or any(ord(c) < 0x20 or ord(c) == 0x7f for c in key + value)):
                raise OAuthHTTPError("invalid_oauth_form")
            normalized.append((key, value))
        try:
            body = urlencode(normalized).encode("ascii")
        except (UnicodeEncodeError, ValueError):
            raise OAuthHTTPError("invalid_oauth_form") from None
        if len(body) > self.MAX_REQUEST_BYTES:
            raise OAuthHTTPError("oauth_request_too_large")
        host, target = self._parse_endpoint(endpoint)
        pinned_ip = self._resolve_public_ip(host)
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "Yuki-OAuth/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        request_headers.update(self._custom_headers(
            headers,
            reserved={"host", "connection", "content-length", "transfer-encoding", "content-type"},
        ))
        connection = self._connection_factory(
            host, pinned_ip, port=443, timeout=self.timeout, context=self._ssl_context
        )
        try:
            connection.request("POST", target, body=body, headers=request_headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise OAuthHTTPError("oauth_redirect_rejected")
            # Read only a small bounded diagnostic body; never return it.
            response.read(min(self.MAX_RESPONSE_BYTES, 16_384) + 1)
            if response.status < 200 or response.status >= 300:
                raise OAuthHTTPError("oauth_revocation_rejected")
            return response.status
        except OAuthHTTPError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError):
            raise OAuthHTTPError("oauth_endpoint_unavailable") from None
        finally:
            connection.close()

    def request_form(self, endpoint, *, fields, headers=None):
        """POST standard application/x-www-form-urlencoded OAuth fields."""
        if not isinstance(fields, dict) or not fields:
            raise OAuthHTTPError("invalid_oauth_form")
        normalized = []
        for key, value in fields.items():
            if (not isinstance(key, str) or not key or len(key) > 128
                    or not isinstance(value, str) or len(value) > 8192
                    or any(ord(c) < 0x20 or ord(c) == 0x7f for c in key + value)):
                raise OAuthHTTPError("invalid_oauth_form")
            normalized.append((key, value))
        try:
            body = urlencode(normalized).encode("ascii")
        except (UnicodeEncodeError, ValueError):
            raise OAuthHTTPError("invalid_oauth_form") from None
        if len(body) > self.MAX_REQUEST_BYTES:
            raise OAuthHTTPError("oauth_request_too_large")
        host, target = self._parse_endpoint(endpoint)
        pinned_ip = self._resolve_public_ip(host)
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "Yuki-OAuth/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        custom_headers = self._custom_headers(
            headers,
            reserved={"host", "connection", "content-length", "transfer-encoding", "content-type"},
        )
        request_headers.update(custom_headers)
        connection = self._connection_factory(
            host, pinned_ip, port=443, timeout=self.timeout, context=self._ssl_context
        )
        try:
            connection.request("POST", target, body=body, headers=request_headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise OAuthHTTPError("oauth_redirect_rejected")
            if response.status < 200 or response.status >= 300:
                raise OAuthHTTPError("oauth_http_request_failed")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise OAuthHTTPError("oauth_response_content_type_invalid")
            raw = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(raw) > self.MAX_RESPONSE_BYTES:
                raise OAuthHTTPError("oauth_response_too_large")
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise OAuthHTTPError("oauth_response_invalid_json") from None
            if not isinstance(value, dict):
                raise OAuthHTTPError("oauth_response_invalid_shape")
            return value
        except OAuthHTTPError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError):
            raise OAuthHTTPError("oauth_endpoint_unavailable") from None
        finally:
            connection.close()

