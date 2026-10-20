"""Small, fail-closed mobile API for a trusted Yuki host.

The API deliberately exposes status, model discovery, and text-only inference. It
never executes skills or accepts approval decisions from a remote client. Bind to
loopback by default; non-loopback binding requires TLS and an explicit bearer token.
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from core.config import load_config
from providers.factory import create_provider

_LOG = logging.getLogger("yuki.mobile")
_WEB_ROOT = Path(__file__).resolve().parent / "web"
_MAX_BODY = 16_384
_MAX_PROMPT = 8_000


class MobileAPIError(RuntimeError):
    """Configuration or request error safe to report without secret values."""


def _is_loopback(host: str) -> bool:
    import ipaddress

    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def create_handler(*, token: str, provider, model_name: str = "local model"):
    """Build an isolated handler class with a fixed token/provider and bounded requests."""
    if not isinstance(token, str) or len(token) < 32:
        raise MobileAPIError("YUKI_MOBILE_TOKEN must contain at least 32 characters.")
    if provider is None or not callable(getattr(provider, "generate", None)):
        raise MobileAPIError("A configured inference provider is required.")
    lock = threading.Lock()
    windows: dict[str, list[float]] = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "YukiMobile/1"
        sys_version = ""

        def log_message(self, fmt, *args):
            # Avoid default logs containing URLs, prompt fragments, or user data.
            _LOG.info("mobile.http_request", extra={"event": "mobile.http_request", "component": "mobile_api", "status": str(args[1] if len(args) > 1 else "request")[:40]})

        def _reply(self, status: int, payload: object, *, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            return hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))

        def _rate_limited(self) -> bool:
            now = time.monotonic()
            client = self.client_address[0]
            with lock:
                recent = [stamp for stamp in windows.get(client, []) if now - stamp < 60]
                if len(recent) >= 60:
                    windows[client] = recent
                    return True
                recent.append(now)
                windows[client] = recent
                if len(windows) > 512:
                    # Bound memory even if many source addresses connect.
                    for key in list(windows)[:len(windows) - 512]:
                        windows.pop(key, None)
            return False

        def _read_json(self):
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdigit():
                raise ValueError("invalid_content_length")
            length = int(raw_length)
            if length < 1 or length > _MAX_BODY:
                raise OverflowError("body_too_large")
            raw = self.rfile.read(length)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("invalid_json_object")
            return value

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/" or path in {"/index.html", "/manifest.webmanifest", "/app.js"}:
                names = {"/": "index.html", "/index.html": "index.html", "/manifest.webmanifest": "manifest.webmanifest", "/app.js": "app.js"}
                file_path = _WEB_ROOT / names[path]
                try:
                    body = file_path.read_bytes()
                except OSError:
                    self._reply(503, {"error": "mobile_client_unavailable"})
                    return
                content_type = "application/manifest+json" if path == "/manifest.webmanifest" else ("text/javascript; charset=utf-8" if path == "/app.js" else "text/html; charset=utf-8")
                self._reply(200, body, content_type=content_type)
                return
            if path not in {"/api/v1/health", "/api/v1/models"}:
                self._reply(404, {"error": "not_found"})
                return
            if not self._authorized():
                self._reply(401, {"error": "unauthorized"})
                return
            if self._rate_limited():
                self._reply(429, {"error": "rate_limited"})
                return
            if path == "/api/v1/health":
                self._reply(200, {"ok": True, "service": "yuki-mobile-api", "inference": "configured"})
                return
            models = provider.list_models() if callable(getattr(provider, "list_models", None)) else []
            selected = getattr(provider, "model", "") or model_name
            self._reply(200, {"provider": provider.__class__.__name__, "selected_model": selected, "installed_models": models[:100]})

        def do_POST(self):
            if urlsplit(self.path).path != "/api/v1/chat":
                self._reply(404, {"error": "not_found"})
                return
            if not self._authorized():
                self._reply(401, {"error": "unauthorized"})
                return
            if self._rate_limited():
                self._reply(429, {"error": "rate_limited"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._reply(415, {"error": "application_json_required"})
                return
            try:
                body = self._read_json()
            except OverflowError:
                self._reply(413, {"error": "request_too_large"})
                return
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._reply(400, {"error": "invalid_request"})
                return
            prompt = body.get("message")
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > _MAX_PROMPT:
                self._reply(400, {"error": "message_must_be_1_to_8000_characters"})
                return
            try:
                answer = provider.generate(prompt.strip())
            except Exception:
                _LOG.warning("mobile.inference_failed", extra={"event": "mobile.inference_failed", "component": "mobile_api", "status": "failed", "reason_code": "provider_error"})
                self._reply(503, {"error": "inference_unavailable"})
                return
            if not isinstance(answer, str):
                self._reply(503, {"error": "invalid_provider_response"})
                return
            self._reply(200, {"answer": answer[:12_000], "execution": "text_only_no_skills_executed"})

        def do_PUT(self):
            self._reply(405, {"error": "method_not_allowed"})

        def do_DELETE(self):
            self._reply(405, {"error": "method_not_allowed"})

    return Handler


def build_server(*, host: str | None = None, port: int | None = None, config_path=None, environ=None):
    """Create a configured server. Remote bind requires TLS and a bearer token."""
    env = os.environ if environ is None else environ
    bind_host = (host or env.get("YUKI_MOBILE_HOST", "127.0.0.1")).strip()
    bind_port = port if port is not None else int(env.get("YUKI_MOBILE_PORT", "8765"))
    if not bind_host or not 1 <= bind_port <= 65535:
        raise MobileAPIError("Mobile API host/port configuration is invalid.")
    token = env.get("YUKI_MOBILE_TOKEN", "")
    if len(token) < 32:
        raise MobileAPIError("Set YUKI_MOBILE_TOKEN to a random secret of at least 32 characters.")
    config = load_config(config_path)
    provider = create_provider(config, environ=env)
    handler = create_handler(token=token, provider=provider, model_name=config.get("ollama_model") or "local model")
    server = ThreadingHTTPServer((bind_host, bind_port), handler)
    server.daemon_threads = True
    if not _is_loopback(bind_host):
        cert = env.get("YUKI_MOBILE_TLS_CERT", "")
        key = env.get("YUKI_MOBILE_TLS_KEY", "")
        if not cert or not key:
            server.server_close()
            raise MobileAPIError("Non-loopback binding requires YUKI_MOBILE_TLS_CERT and YUKI_MOBILE_TLS_KEY.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(certfile=cert, keyfile=key)
        except (OSError, ssl.SSLError) as exc:
            server.server_close()
            raise MobileAPIError("Could not load the configured TLS certificate/key.") from exc
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Yuki's authenticated mobile API")
    parser.add_argument("--host", default=None, help="defaults to 127.0.0.1; remote bind requires TLS")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="optional config YAML path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        server = build_server(host=args.host, port=args.port, config_path=args.config)
    except (MobileAPIError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    scheme = "https" if isinstance(server.socket, ssl.SSLSocket) else "http"
    print(f"Yuki mobile API listening on {scheme}://{server.server_address[0]}:{server.server_address[1]}")
    print("Mobile API exposes text inference only; it cannot execute skills or approve actions.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
