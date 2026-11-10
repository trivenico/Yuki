"""DNS-pinned, redirect-free HTTPS transport for MCP JSON messages."""
from __future__ import annotations
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlsplit


class MCPPinnedTransportError(RuntimeError):
    """Stable transport error without endpoint or credential disclosure."""


class _PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip, *, timeout, context):
        super().__init__(host, port=443, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class PinnedMCPHTTPTransport:
    MAX_REQUEST_BYTES = 64_000
    MAX_RESPONSE_BYTES = 1_000_000
    _HEADER = re.compile(r"[!#$%&'*+.^_|~0-9A-Za-z-]+")

    def __init__(self, *, allowed_hosts, timeout=10, resolver=None, connection_factory=None):
        if not isinstance(allowed_hosts, (set, frozenset, tuple, list)) or not allowed_hosts:
            raise ValueError("an explicit MCP host allow-list is required")
        hosts = set()
        for host in allowed_hosts:
            if not isinstance(host, str) or not host or host != host.strip():
                raise ValueError("invalid MCP host allow-list")
            hosts.add(host.lower().rstrip("."))
        self.allowed_hosts = frozenset(hosts)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        self.timeout = float(timeout)
        self._resolver = resolver or socket.getaddrinfo
        self._factory = connection_factory or _PinnedConnection
        self._ssl_context = ssl.create_default_context()

    def _endpoint(self, endpoint):
        if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 2048:
            raise MCPPinnedTransportError("invalid_mcp_endpoint")
        if any(ord(c) < 0x21 or ord(c) == 0x7f for c in endpoint):
            raise MCPPinnedTransportError("invalid_mcp_endpoint")
        try:
            parts = urlsplit(endpoint)
            port = parts.port
        except ValueError:
            raise MCPPinnedTransportError("invalid_mcp_endpoint") from None
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or port not in (None, 443)
                or parts.fragment or host not in self.allowed_hosts):
            raise MCPPinnedTransportError("untrusted_mcp_endpoint")
        path = parts.path or "/"
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise MCPPinnedTransportError("invalid_mcp_endpoint")
        return host, path + (("?" + parts.query) if parts.query else "")

    def _resolve(self, host):
        try:
            records = self._resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise MCPPinnedTransportError("mcp_resolution_failed") from None
        addresses = set()
        for record in records:
            try:
                ip = ipaddress.ip_address(record[4][0])
            except (ValueError, IndexError, TypeError):
                raise MCPPinnedTransportError("mcp_resolution_failed") from None
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if not ip.is_global:
                raise MCPPinnedTransportError("mcp_destination_blocked")
            addresses.add(str(ip))
        if not addresses:
            raise MCPPinnedTransportError("mcp_resolution_failed")
        return sorted(addresses)[0]

    @classmethod
    def _headers(cls, headers):
        if not isinstance(headers, dict):
            raise MCPPinnedTransportError("invalid_mcp_headers")
        result, seen = {}, set()
        reserved = {"host", "connection", "content-length", "transfer-encoding"}
        for key, value in headers.items():
            if (not isinstance(key, str) or not cls._HEADER.fullmatch(key)
                    or not isinstance(value, str)
                    or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value)):
                raise MCPPinnedTransportError("invalid_mcp_headers")
            normalized = key.lower()
            if normalized in reserved or normalized in seen:
                raise MCPPinnedTransportError("invalid_mcp_headers")
            seen.add(normalized)
            result[key] = value
        return result

    @staticmethod
    def _read_sse_response(response, expected_id):
        """Read SSE incrementally and stop at the matching JSON-RPC response."""
        if type(expected_id) not in (str, int) or isinstance(expected_id, bool):
            raise MCPPinnedTransportError("mcp_sse_request_id_invalid")
        pending = bytearray()
        event_data = []
        event_size = 0
        event_type = "message"
        total_size = 0
        max_line = 65_536
        max_event = 262_144
        max_total = PinnedMCPHTTPTransport.MAX_RESPONSE_BYTES

        def consume_line(line):
            nonlocal event_data, event_size, event_type
            if len(line) > max_line:
                raise MCPPinnedTransportError("mcp_sse_line_too_large")
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line:
                if not event_data:
                    event_size = 0
                    event_type = "message"
                    return None
                raw = b"\n".join(event_data)
                current_type = event_type
                event_data = []
                event_size = 0
                event_type = "message"
                if current_type not in ("", "message"):
                    return None
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise MCPPinnedTransportError("mcp_sse_invalid_event") from None
                if isinstance(message, dict) and message.get("id") == expected_id:
                    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                return None
            if line.startswith(b":"):
                return None
            field, separator, value = line.partition(b":")
            if separator and value.startswith(b" "):
                value = value[1:]
            if field == b"event":
                try:
                    event_type = value.decode("utf-8")
                except UnicodeDecodeError:
                    raise MCPPinnedTransportError("mcp_sse_invalid_event") from None
            elif field == b"data":
                event_size += len(value) + (1 if event_data else 0)
                if event_size > max_event:
                    raise MCPPinnedTransportError("mcp_sse_event_too_large")
                event_data.append(value)
            return None

        while True:
            try:
                chunk = response.read1(4096)
            except (AttributeError, OSError, http.client.HTTPException, TimeoutError):
                raise MCPPinnedTransportError("mcp_sse_stream_failed") from None
            if not chunk:
                raise MCPPinnedTransportError("mcp_sse_response_missing")
            total_size += len(chunk)
            if total_size > max_total:
                raise MCPPinnedTransportError("mcp_response_too_large")
            pending.extend(chunk)
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    if len(pending) > max_line:
                        raise MCPPinnedTransportError("mcp_sse_line_too_large")
                    break
                line = bytes(pending[:newline])
                del pending[:newline + 1]
                result = consume_line(line)
                if result is not None:
                    return result

    def post_json(self, endpoint, *, payload, headers):
        try:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            raise MCPPinnedTransportError("invalid_mcp_payload") from None
        if len(body) > self.MAX_REQUEST_BYTES:
            raise MCPPinnedTransportError("mcp_request_too_large")
        host, target = self._endpoint(endpoint)
        pinned_ip = self._resolve(host)
        request_headers = {"Accept": "application/json, text/event-stream",
                           "Content-Type": "application/json",
                           "User-Agent": "Yuki-MCP/1.0"}
        request_headers.update(self._headers(headers))
        connection = self._factory(host, pinned_ip, timeout=self.timeout, context=self._ssl_context)
        try:
            connection.request("POST", target, body=body, headers=request_headers)
            response = connection.getresponse()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            if 300 <= response.status < 400:
                raise MCPPinnedTransportError("mcp_redirect_rejected")
            if response.status not in (200, 202, 204):
                raise MCPPinnedTransportError("mcp_http_request_failed")
            if response.status in (202, 204):
                return response.status, response_headers, b""
            content_type = response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type == "text/event-stream":
                if not isinstance(payload, dict) or "id" not in payload:
                    raise MCPPinnedTransportError("mcp_sse_notification_not_supported")
                raw = self._read_sse_response(response, payload["id"])
                response_headers["content-type"] = "application/json"
                return response.status, response_headers, raw
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise MCPPinnedTransportError("mcp_response_content_type_invalid")
            raw = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(raw) > self.MAX_RESPONSE_BYTES:
                raise MCPPinnedTransportError("mcp_response_too_large")
            return response.status, response_headers, raw
        except MCPPinnedTransportError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError):
            raise MCPPinnedTransportError("mcp_endpoint_unavailable") from None
        finally:
            connection.close()
