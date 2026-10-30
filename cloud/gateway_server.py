"""Minimal authenticated HTTP boundary for the Yuki Cloud Gateway.

Run behind a TLS-terminating reverse proxy. Bind to loopback by default.
Enrollment/revocation require a high-entropy operator bootstrap secret; never
expose this bootstrap API as an unauthenticated public registration endpoint.
"""
from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from cloud.device_registry import DeviceCredentialError, DeviceCredentialStore


MAX_BODY_BYTES = 16 * 1024


def _bearer(header):
    if not isinstance(header, str):
        return None
    scheme, separator, token = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token or token.strip() != token:
        return None
    return token


def make_handler(store, admin_token):
    if not isinstance(admin_token, str) or len(admin_token) < 32:
        raise ValueError("YUKI_GATEWAY_ADMIN_TOKEN must contain at least 32 characters.")

    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "YukiGateway"
        sys_version = ""

        def log_message(self, fmt, *args):
            # Avoid logging request headers, credentials, or request bodies.
            return

        def _json(self, status, payload):
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def _body(self):
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdigit():
                raise ValueError("A valid Content-Length is required.")
            length = int(raw_length)
            if length > MAX_BODY_BYTES:
                raise OverflowError
            raw = self.rfile.read(length)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object.")
            return value

        def _device_principal(self, required_scope):
            token = _bearer(self.headers.get("Authorization"))
            principal = store.authenticate(token)
            if required_scope not in principal.scopes:
                self._json(403, {"error": "insufficient_scope"})
                return None
            return principal

        def _is_admin(self):
            supplied = _bearer(self.headers.get("Authorization"))
            return supplied is not None and hmac.compare_digest(supplied, admin_token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._json(200, {"status": "ok", "service": "yuki-cloud-gateway"})
                return
            try:
                if path == "/v1/me":
                    principal = self._device_principal("profile:read")
                    if principal is not None:
                        self._json(200, {
                            "device_id": principal.device_id,
                            "user_id": principal.user_id,
                            "device_name": principal.device_name,
                            "scopes": sorted(principal.scopes),
                        })
                    return
                if path == "/v1/devices":
                    principal = self._device_principal("devices:read")
                    if principal is not None:
                        self._json(200, {"devices": store.list_for_user(user_id=principal.user_id)})
                    return
            except DeviceCredentialError:
                self._json(401, {"error": "unauthorized"})
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != "/v1/admin/devices":
                self._json(404, {"error": "not_found"})
                return
            if not self._is_admin():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                body = self._body()
                issued = store.issue(
                    user_id=body.get("user_id"),
                    device_name=body.get("device_name"),
                    scopes=body.get("scopes", ("profile:read", "chat:write", "tasks:read", "tasks:write")),
                )
            except OverflowError:
                self._json(413, {"error": "request_too_large"})
                return
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_request"})
                return
            self._json(201, {
                "device_id": issued.device_id,
                "device_name": issued.device_name,
                "token": issued.token,
                "created_at": issued.created_at,
            })

        def do_DELETE(self):
            path = urlsplit(self.path).path
            prefix = "/v1/admin/devices/"
            if not path.startswith(prefix) or not path[len(prefix):] or "/" in path[len(prefix):]:
                self._json(404, {"error": "not_found"})
                return
            if not self._is_admin():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                body = self._body()
                revoked = store.revoke(user_id=body.get("user_id"), device_id=path[len(prefix):])
            except OverflowError:
                self._json(413, {"error": "request_too_large"})
                return
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_request"})
                return
            if not revoked:
                self._json(404, {"error": "device_not_found"})
                return
            self._json(200, {"revoked": True})

        def do_OPTIONS(self):
            self._json(405, {"error": "method_not_allowed"})

    return GatewayHandler


def create_server(*, database_path, admin_token, host="127.0.0.1", port=8765):
    store = DeviceCredentialStore(database_path)
    handler = make_handler(store, admin_token)
    return ThreadingHTTPServer((host, int(port)), handler)


def main():
    admin_token = os.environ.get("YUKI_GATEWAY_ADMIN_TOKEN", "")
    database_path = os.environ.get("YUKI_GATEWAY_DATABASE", "/var/lib/yuki/gateway.sqlite3")
    host = os.environ.get("YUKI_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("YUKI_GATEWAY_PORT", "8765"))
    server = create_server(
        database_path=database_path,
        admin_token=admin_token,
        host=host,
        port=port,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
