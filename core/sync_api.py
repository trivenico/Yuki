"""Framework-neutral WSGI API for the Yuki Sync Hub.

Deploy behind a TLS-terminating reverse proxy and inject an authenticator that
validates short-lived credentials and returns the authenticated device ID.
This module intentionally does not implement login or token issuance.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable

from core.sync_store import (
    RevokedDeviceError, SyncConflictError, SyncEventStore,
    SyncStoreError, UnknownDeviceError,
)
from core.sync_auth import SyncAuthenticationError, SyncCredentialStore

MAX_REQUEST_BYTES = 256 * 1024
MAX_PAGE_SIZE = 500


class SyncAPI:
    def __init__(
        self,
        store: SyncEventStore,
        authenticate: Callable[[str], str] | None = None,
        credentials: SyncCredentialStore | None = None,
    ):
        if authenticate is None and credentials is None:
            raise TypeError("Provide credentials or an authenticator.")
        if authenticate is not None and not callable(authenticate):
            raise TypeError("authenticate must be callable.")
        self.store = store
        self.credentials = credentials
        self.authenticate = authenticate or credentials.authenticate_access

    @staticmethod
    def _response(start_response, status: str, payload: dict, headers=None):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        base = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        start_response(status, base + (headers or []))
        return [body]

    @staticmethod
    def _read_json(environ):
        raw_length = environ.get("CONTENT_LENGTH", "")
        if not raw_length or not raw_length.isdigit():
            raise ValueError("Content-Length is required.")
        length = int(raw_length)
        if not 1 <= length <= MAX_REQUEST_BYTES:
            raise ValueError("Request body size is invalid or exceeds 256 KB.")
        stream = environ["wsgi.input"]
        raw = stream.read(length)
        if len(raw) != length:
            raise ValueError("Request body was incomplete.")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def __call__(self, environ, start_response) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        if method == "GET" and path == "/health":
            return self._response(start_response, "200 OK", {"ok": True, "service": "yuki-sync"})
        if method == "POST" and path == "/v1/pair":
            if self.credentials is None:
                return self._response(start_response, "404 Not Found", {"error": "not_found"})
            try:
                raw = self._read_json(environ)
                if set(raw) - {"code", "device_label"} or not isinstance(raw.get("code"), str):
                    raise ValueError("Pair request must contain code and optional device_label.")
                result = self.credentials.consume_pairing_code(
                    raw["code"], raw.get("device_label", "Yuki device")
                )
                return self._response(start_response, "201 Created", result)
            except SyncAuthenticationError:
                return self._response(start_response, "401 Unauthorized", {"error": "invalid_or_expired_pairing_code"})
            except ValueError as exc:
                return self._response(start_response, "400 Bad Request", {"error": str(exc)})
        if method == "POST" and path == "/v1/refresh":
            if self.credentials is None:
                return self._response(start_response, "404 Not Found", {"error": "not_found"})
            try:
                raw = self._read_json(environ)
                if set(raw) != {"refresh_token"} or not isinstance(raw["refresh_token"], str):
                    raise ValueError("Refresh request must contain only refresh_token.")
                result = self.credentials.refresh(raw["refresh_token"])
                return self._response(start_response, "200 OK", result)
            except SyncAuthenticationError:
                return self._response(start_response, "401 Unauthorized", {"error": "invalid_refresh_token"})
            except ValueError as exc:
                return self._response(start_response, "400 Bad Request", {"error": str(exc)})
        auth = environ.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer ") or len(auth) > 4096:
            return self._response(start_response, "401 Unauthorized", {"error": "authentication_required"})
        try:
            try:
                device_id = self.authenticate(auth[7:])
            except SyncAuthenticationError:
                return self._response(start_response, "401 Unauthorized", {"error": "invalid_credentials"})
            except Exception:
                return self._response(start_response, "401 Unauthorized", {"error": "invalid_credentials"})
            if not isinstance(device_id, str) or not device_id:
                return self._response(start_response, "401 Unauthorized", {"error": "invalid_credentials"})
            if method == "POST" and path == "/v1/events":
                raw = self._read_json(environ)
                stored = self.store.append_event(device_id, raw)
                return self._response(start_response, "201 Created", stored.to_dict())
            if method == "GET" and path == "/v1/events":
                from urllib.parse import parse_qs
                query = parse_qs(environ.get("QUERY_STRING", ""), strict_parsing=False)
                after_raw = query.get("after", ["0"])[0]
                limit_raw = query.get("limit", ["100"])[0]
                if not after_raw.isdigit() or not limit_raw.isdigit():
                    raise ValueError("after and limit must be non-negative integers.")
                after, limit = int(after_raw), int(limit_raw)
                if limit > MAX_PAGE_SIZE:
                    raise ValueError("limit cannot exceed 500.")
                events = self.store.pull_events(device_id, after, limit)
                return self._response(start_response, "200 OK", {
                    "events": [item.to_dict() for item in events],
                    "next_after": events[-1].sequence if events else after,
                })
            if method == "POST" and path == "/v1/ack":
                raw = self._read_json(environ)
                if set(raw) != {"sequence"}:
                    raise ValueError("Body must contain only sequence.")
                sequence = self.store.acknowledge(device_id, raw["sequence"])
                return self._response(start_response, "200 OK", {"acknowledged": sequence})
            return self._response(start_response, "404 Not Found", {"error": "not_found"})
        except (ValueError, SyncConflictError) as exc:
            return self._response(start_response, "409 Conflict" if isinstance(exc, SyncConflictError) else "400 Bad Request",
                                  {"error": str(exc)})
        except (UnknownDeviceError, RevokedDeviceError):
            return self._response(start_response, "403 Forbidden", {"error": "device_not_authorized"})
        except SyncStoreError:
            return self._response(start_response, "403 Forbidden", {"error": "sync_operation_rejected"})
        except Exception:
            # Never return exception details or payload content to remote clients.
            return self._response(start_response, "500 Internal Server Error", {"error": "internal_error"})
