"""Separate administrator ASGI control plane for workload signing keys.

The host supplies authentication; publisher/workload credentials are not
accepted as administrator identity. Mount on a separately protected listener.
"""
from __future__ import annotations

import base64
import binascii
import json

from core.workload_identity import WorkloadSigningKey
from core.workload_key_registry import SQLiteWorkloadKeyRegistry, WorkloadKeyRegistryError


class WorkloadKeyAdminASGI:
    """Admin-only registration, activation, retirement, revocation, and audit."""

    MAX_BODY = 4096

    def __init__(self, registry: SQLiteWorkloadKeyRegistry, authenticate_admin):
        if not isinstance(registry, SQLiteWorkloadKeyRegistry):
            raise TypeError("workload_key_registry_required")
        if not callable(authenticate_admin):
            raise TypeError("trusted_admin_authenticator_required")
        self.registry = registry
        self.authenticate_admin = authenticate_admin

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not isinstance(scope.get("path"), str):
            await self._respond(send, 404, {"error": "not_found"})
            return
        try:
            administrator = self.authenticate_admin(scope)
            if not isinstance(administrator, str) or not administrator:
                await self._respond(send, 401, {"error": "unauthorized"})
                return
            path = scope["path"]
            method = scope.get("method", "GET")
            if path == "/v1/admin/keys/audit" and method == "GET":
                events = self.registry.audit_events(administrator)
                await self._respond(send, 200, {"events": list(events)})
                return
            prefix = "/v1/admin/keys/"
            if not path.startswith(prefix):
                await self._respond(send, 404, {"error": "not_found"})
                return
            parts = path[len(prefix):].split("/")
            if len(parts) not in (2, 3) or any(not p or len(p) > 256 for p in parts):
                await self._respond(send, 400, {"error": "invalid_key_path"})
                return
            issuer, key_id = parts[:2]
            if method == "PUT" and len(parts) == 2:
                payload = await self._read_json(receive)
                if set(payload) != {"algorithm", "public_key"} or payload["algorithm"] != "EdDSA":
                    raise ValueError("invalid_key_registration")
                encoded = payload["public_key"]
                if not isinstance(encoded, str) or len(encoded) > 128:
                    raise ValueError("invalid_public_key")
                try:
                    public_key = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("invalid_public_key") from exc
                if len(public_key) != 32 or base64.b64encode(public_key).decode("ascii") != encoded:
                    raise ValueError("invalid_public_key")
                self.registry.register(administrator, WorkloadSigningKey(
                    issuer, key_id, "EdDSA", public_key))
                await self._respond(send, 201, {"status": "staged"})
                return
            if method == "POST" and len(parts) == 3:
                action = parts[2]
                operations = {
                    "activate": self.registry.activate,
                    "retire": self.registry.retire,
                    "revoke": self.registry.revoke,
                }
                operation = operations.get(action)
                if operation is None:
                    await self._respond(send, 404, {"error": "not_found"})
                    return
                await self._read_empty_body(receive)
                operation(administrator, issuer, key_id)
                await self._respond(send, 204, {})
                return
            await self._respond(send, 405, {"error": "method_not_allowed"},
                                [(b"allow", b"GET, PUT, POST")])
        except WorkloadKeyRegistryError as exc:
            status = 403 if "forbidden" in str(exc) else 409
            await self._respond(send, status, {"error": "key_operation_rejected"})
        except (ValueError, UnicodeError, json.JSONDecodeError):
            await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            await self._respond(send, 503, {"error": "service_unavailable"})

    async def _read_body(self, receive):
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message.get("type") != "http.request":
                raise ValueError("invalid_request_body")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes) or len(body) + len(chunk) > self.MAX_BODY:
                raise ValueError("request_body_too_large")
            body.extend(chunk)
            more = message.get("more_body", False)
        return bytes(body)

    async def _read_json(self, receive):
        raw = await self._read_body(receive)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate_json_member")
                result[key] = value
            return result
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError("json_object_required")
        return value

    async def _read_empty_body(self, receive):
        if await self._read_body(receive):
            raise ValueError("unexpected_request_body")

    @staticmethod
    async def _respond(send, status, payload, extra_headers=()):
        body = b"" if status == 204 else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-length", str(len(body)).encode("ascii")),
            *extra_headers,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
