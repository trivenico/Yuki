"""Authenticated owner-scoped provider profile management API.

Secrets are accepted only for vault insertion and are never returned. This API
manages profile metadata and selection; it deliberately does not mutate a
process-global provider adapter. Runtime activation requires a per-owner model
execution context and is a separate integration boundary.
"""
from __future__ import annotations

import asyncio
import json
import re

from providers.profile_store import ProviderProfileStore

_MAX_BODY = 12_000
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")


class AuthenticatedProviderProfileAPI:
    """Expose persistent provider profile lifecycle to authenticated owners."""

    def __init__(self, app, auth_service, store: ProviderProfileStore):
        if not isinstance(store, ProviderProfileStore):
            raise TypeError("store must be a ProviderProfileStore")
        if not callable(getattr(auth_service, "resolve_session", None)):
            raise TypeError("auth_service must resolve authenticated sessions")
        self.app = app
        self.auth = auth_service
        self.store = store

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        if path != "/v1/provider-profiles" and not path.startswith("/v1/provider-profiles/"):
            await self.app(scope, receive, send)
            return
        try:
            user = await self._user(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})

            if path == "/v1/provider-profiles" and method == "GET":
                profiles = await asyncio.to_thread(self.store.list_profiles, user.user_id)
                selected = await asyncio.to_thread(self.store.selected_profile, user.user_id)
                safe_profiles = []
                for profile in profiles:
                    safe_profiles.append(await self._project(profile, selected))
                return await self._respond(send, 200, {
                    "profiles": safe_profiles,
                    "active_profile": selected,
                })

            if path == "/v1/provider-profiles" and method == "POST":
                content_type = dict(scope.get("headers", [])).get(b"content-type", b"")
                if not content_type.lower().startswith(b"application/json"):
                    return await self._respond(send, 415, {"error": "application_json_required"})
                payload = await self._body(receive)
                required = {"name", "base_url", "model", "approved_host", "api_key", "confirm_cloud"}
                if (set(payload) != required
                        or not all(isinstance(payload.get(k), str)
                                   for k in ("name", "base_url", "model", "approved_host", "api_key"))
                        or payload.get("confirm_cloud") is not True):
                    return await self._respond(send, 400, {"error": "explicit_cloud_confirmation_required"})
                if not _NAME.fullmatch(payload["name"]):
                    return await self._respond(send, 400, {"error": "invalid_profile_name"})
                try:
                    profile = await asyncio.to_thread(
                        self.store.save,
                        owner_id=user.user_id,
                        name=payload["name"],
                        base_url=payload["base_url"],
                        model=payload["model"],
                        allowed_host=payload["approved_host"],
                        api_key=payload["api_key"],
                    )
                except ValueError:
                    return await self._respond(send, 400, {"error": "invalid_profile"})
                except Exception:
                    return await self._respond(send, 503, {"error": "credential_vault_unavailable"})
                selected = await asyncio.to_thread(self.store.selected_profile, user.user_id)
                return await self._respond(send, 201, {"profile": await self._project(profile, selected)})

            if path == "/v1/provider-profiles/active" and method == "GET":
                selected = await asyncio.to_thread(self.store.selected_profile, user.user_id)
                if selected is None:
                    return await self._respond(send, 200, {"active_profile": None})
                profiles = await asyncio.to_thread(self.store.list_profiles, user.user_id)
                profile = next((p for p in profiles if p.name == selected), None)
                return await self._respond(send, 200, {
                    "active_profile": await self._project(profile, selected) if profile else None
                })

            if path == "/v1/provider-profiles/active" and method == "PUT":
                payload = await self._body(receive)
                if set(payload) != {"name"} or not isinstance(payload.get("name"), str):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    configured = await asyncio.to_thread(
                        self.store.credential_available, user.user_id, payload["name"]
                    )
                    if configured is not True:
                        return await self._respond(send, 409, {"error": "profile_credential_unavailable"})
                    await asyncio.to_thread(self.store.select_profile, user.user_id, payload["name"])
                except KeyError:
                    return await self._respond(send, 404, {"error": "profile_not_found"})
                except ValueError:
                    return await self._respond(send, 400, {"error": "invalid_profile_name"})
                return await self._respond(send, 200, {"active_profile": payload["name"]})

            match = re.fullmatch(r"/v1/provider-profiles/([a-z][a-z0-9_-]{1,47})/verify", path)
            if match and method == "POST":
                profiles = await asyncio.to_thread(self.store.list_profiles, user.user_id)
                if not any(p.name == match.group(1) for p in profiles):
                    return await self._respond(send, 404, {"error": "profile_not_found"})
                ready = await asyncio.to_thread(
                    self.store.credential_available, user.user_id, match.group(1)
                )
                return await self._respond(send, 200, {
                    "profile": match.group(1),
                    "credential_configured": ready,
                    "verification": "local_vault_only",
                    "network_probe_performed": False,
                })

            match = re.fullmatch(r"/v1/provider-profiles/([a-z][a-z0-9_-]{1,47})", path)
            if match and method == "DELETE":
                deleted = await asyncio.to_thread(self.store.delete, user.user_id, match.group(1))
                return await self._respond(send, 200 if deleted else 404,
                                           {"deleted": True} if deleted else {"error": "profile_not_found"})
            return await self._respond(send, 404, {"error": "not_found"})
        except ValueError:
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 500, {"error": "internal_error"})

    async def _user(self, scope):
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return await asyncio.to_thread(self.auth.resolve_session, token)

    @staticmethod
    async def _body(receive):
        chunks = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                raise ValueError("client disconnected")
            if event["type"] != "http.request":
                continue
            chunks.extend(event.get("body", b""))
            if len(chunks) > _MAX_BODY:
                raise ValueError("request body too large")
            if not event.get("more_body", False):
                break
        try:
            value = json.loads(chunks.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("invalid JSON") from None
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    async def _project(self, profile, selected):
        credential_configured = await asyncio.to_thread(
            self.store.credential_available, profile.owner_id, profile.name
        )
        return {
            "name": profile.name,
            "base_url": profile.base_url,
            "model": profile.model,
            "approved_host": profile.allowed_host,
            "credential_configured": credential_configured,
            "selected": profile.name == selected,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
