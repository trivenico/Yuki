"""Authenticated owner-only API for relationship persona preferences."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Callable

from companion.relationship_mode import AffectionLevel, RelationshipMode, RelationshipModeSettings
from companion.relationship_settings_store import RelationshipSettingsStore


_PREFIX = "/v1/companion/relationship-settings"
_MAX_BODY = 16_384
_FIELDS = frozenset({
    "mode", "affection", "adult_confirmed", "memory_enabled",
    "proactive_checkins_enabled", "quiet_hours_start", "quiet_hours_end",
    "topics_to_avoid",
})


class AuthenticatedRelationshipSettingsAPI:
    """Derive owner identity exclusively from the authenticated session."""

    def __init__(self, app, auth_service, store_for_owner: Callable[[str], RelationshipSettingsStore]):
        if not callable(store_for_owner):
            raise TypeError("store_for_owner must be callable")
        self.app = app
        self.auth = auth_service
        self.store_for_owner = store_for_owner

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != _PREFIX:
            await self.app(scope, receive, send)
            return
        try:
            user = await self._user(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            method = scope.get("method", "GET").upper()
            if method not in {"GET", "PUT", "DELETE"}:
                return await self._respond(send, 405, {"error": "method_not_allowed"})
            if method == "PUT" and not self._has_json_content_type(scope):
                return await self._respond(send, 415, {"error": "unsupported_media_type"})
            store = await asyncio.to_thread(self.store_for_owner, user.user_id)
            if not isinstance(store, RelationshipSettingsStore) or store.owner_id != user.user_id:
                return await self._respond(send, 500, {"error": "storage_unavailable"})
            if method == "GET":
                settings = await asyncio.to_thread(store.load)
                return await self._respond(send, 200, {"settings": self._serialize(settings)})
            if method == "DELETE":
                await asyncio.to_thread(store.reset)
                return await self._respond(
                    send, 200, {"settings": self._serialize(RelationshipModeSettings()), "reset": True}
                )
            if method == "PUT":
                payload = await self._body(receive)
                if set(payload) != _FIELDS:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if not isinstance(payload["topics_to_avoid"], list):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                settings = RelationshipModeSettings(
                    mode=RelationshipMode(payload["mode"]),
                    affection=AffectionLevel(payload["affection"]),
                    adult_confirmed=payload["adult_confirmed"],
                    memory_enabled=payload["memory_enabled"],
                    proactive_checkins_enabled=payload["proactive_checkins_enabled"],
                    quiet_hours_start=payload["quiet_hours_start"],
                    quiet_hours_end=payload["quiet_hours_end"],
                    topics_to_avoid=tuple(payload["topics_to_avoid"]),
                )
                await asyncio.to_thread(store.save, settings)
                return await self._respond(send, 200, {"settings": self._serialize(settings)})
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 500, {"error": "internal_error"})

    async def _user(self, scope):
        authorization = [
            value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and key.lower() == b"authorization"
            and isinstance(value, bytes)
        ]
        if len(authorization) != 1:
            return None
        parts = authorization[0].decode("latin-1").split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 256:
            return None
        return await asyncio.to_thread(self.auth.resolve_session, parts[1])

    @staticmethod
    def _has_json_content_type(scope):
        values = [
            value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and key.lower() == b"content-type"
            and isinstance(value, bytes)
        ]
        if len(values) != 1:
            return False
        media_type = values[0].decode("latin-1").split(";", 1)[0].strip().lower()
        return media_type == "application/json"

    @staticmethod
    async def _body(receive):
        chunks = bytearray()
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                raise ValueError("client_disconnected")
            if event.get("type") != "http.request":
                continue
            chunks.extend(event.get("body", b""))
            if len(chunks) > _MAX_BODY:
                raise ValueError("request_too_large")
            if not event.get("more_body", False):
                break
        value = json.loads(chunks.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("request_must_be_object")
        return value

    @staticmethod
    def _serialize(settings: RelationshipModeSettings) -> dict:
        value = asdict(settings)
        value["mode"] = settings.mode.value
        value["affection"] = settings.affection.value
        value["topics_to_avoid"] = list(settings.topics_to_avoid)
        return value

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
