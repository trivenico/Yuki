"""Authenticated, bounded WSGI API for a local Yuki desktop client.

Bind the hosting server exclusively to 127.0.0.1. This module deliberately
emits no CORS headers and rejects browser-origin requests unless explicitly
allow-listed by the host application.
"""
from __future__ import annotations

import hmac
import json
from typing import Iterable

MAX_REQUEST_BYTES = 64 * 1024


class LocalAPI:
    def __init__(self, runtime, token: str, allowed_origins=()):
        if runtime is None:
            raise ValueError("A Yuki application runtime is required.")
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("Local API token must contain at least 32 characters.")
        origins = tuple(allowed_origins)
        if any(not isinstance(item, str) or not item.startswith(("http://", "https://")) for item in origins):
            raise ValueError("Allowed origins must be explicit HTTP(S) origins.")
        self.runtime = runtime
        self._token = token
        self.allowed_origins = frozenset(origins)

    @staticmethod
    def _respond(start_response, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        start_response(status, [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
        ])
        return [body]

    @staticmethod
    def _read_body(environ):
        raw_length = environ.get("CONTENT_LENGTH", "")
        if not raw_length or not raw_length.isdigit():
            raise ValueError("Content-Length is required.")
        length = int(raw_length)
        if not 1 <= length <= MAX_REQUEST_BYTES:
            raise ValueError("Request body is empty or exceeds 64 KB.")
        raw = environ["wsgi.input"].read(length)
        if len(raw) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def __call__(self, environ, start_response) -> Iterable[bytes]:
        if environ.get("REQUEST_METHOD", "").upper() == "OPTIONS":
            return self._respond(start_response, "405 Method Not Allowed", {"error": "method_not_allowed"})
        origin = environ.get("HTTP_ORIGIN")
        if origin and origin not in self.allowed_origins:
            return self._respond(start_response, "403 Forbidden", {"error": "origin_not_allowed"})
        if environ.get("REMOTE_ADDR") not in ("127.0.0.1", "::1"):
            return self._respond(start_response, "403 Forbidden", {"error": "loopback_only"})
        authorization = environ.get("HTTP_AUTHORIZATION", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode("utf-8"), self._token.encode("utf-8")):
            return self._respond(start_response, "401 Unauthorized", {"error": "authentication_required"})

        method = environ.get("REQUEST_METHOD", "").upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/v1/health":
                return self._respond(start_response, "200 OK", {"ok": True, "service": "yuki-local"})
            if method == "GET" and path == "/v1/history":
                return self._respond(start_response, "200 OK", {
                    "conversation_id": self.runtime.active_conversation_id,
                    "messages": self.runtime.history(),
                })
            if method == "POST" and path == "/v1/chat":
                payload = self._read_body(environ)
                if set(payload) != {"message"} or not isinstance(payload["message"], str):
                    raise ValueError("Body must contain only a string message.")
                response = self.runtime.send_message(payload["message"])
                return self._respond(start_response, "200 OK", {"response": response})
            if method == "POST" and path == "/v1/conversations":
                payload = self._read_body(environ)
                if set(payload) - {"title"} or ("title" in payload and not isinstance(payload["title"], str)):
                    raise ValueError("Body may contain only a string title.")
                conversation_id = self.runtime.new_conversation(payload.get("title", "Yuki session"))
                return self._respond(start_response, "201 Created", {"conversation_id": conversation_id})
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        except ValueError as exc:
            return self._respond(start_response, "400 Bad Request", {"error": str(exc)})
        except RuntimeError:
            return self._respond(start_response, "503 Service Unavailable", {"error": "runtime_unavailable"})
        except Exception:
            return self._respond(start_response, "500 Internal Server Error", {"error": "internal_error"})
