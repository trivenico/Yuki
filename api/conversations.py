"""Dependency-free ASGI HTTP API for authenticated Yuki conversations.

Account creation/bootstrap is intentionally out of band. The embedding
application constructs auth, provider, guarded tools and conversation services.
No request-supplied owner IDs or tool schemas are trusted.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from core.auth import AuthenticationError, BuiltInAuthService
from core.conversation_service import ConversationService
from core.conversation_store import (
    ConversationConflict,
    ConversationNotFound,
    ConversationError,
)

_MAX_BODY_BYTES = 24_000
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9-]{16,64}$")


class YukiConversationAPI:
    """Small ASGI 3 application for login, logout and persistent chat."""

    def __init__(
        self,
        auth_service: BuiltInAuthService,
        conversation_service: ConversationService,
        *,
        tools_for_user: Callable[[Any], list[dict[str, Any]]] | None = None,
    ):
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService.")
        if not isinstance(conversation_service, ConversationService):
            raise TypeError("conversation_service must be a ConversationService.")
        if tools_for_user is not None and not callable(tools_for_user):
            raise TypeError("tools_for_user must be callable.")
        self.auth = auth_service
        self.conversations = conversation_service
        self.tools_for_user = tools_for_user or (lambda user: [])

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            raise RuntimeError("YukiConversationAPI supports HTTP ASGI scopes only.")
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        try:
            if method == "POST" and path == "/v1/auth/login":
                payload = await self._body(receive)
                if set(payload) != {"username", "password"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    issued = await asyncio.to_thread(
                        self.auth.authenticate, payload["username"], payload["password"]
                    )
                except (AuthenticationError, ValueError):
                    return await self._respond(send, 401, {"error": "invalid_credentials"})
                return await self._respond(send, 200, {
                    "access_token": issued.token,
                    "token_type": "Bearer",
                    "expires_at": issued.expires_at,
                    "user": {
                        "user_id": issued.user.user_id,
                        "username": issued.user.username,
                        "role": issued.user.role,
                    },
                })

            if method == "POST" and path == "/v1/auth/logout":
                user, token = await self._principal(scope)
                if user is None:
                    return await self._respond(send, 401, {"error": "authentication_required"})
                await asyncio.to_thread(self.auth.revoke_session, token)
                return await self._respond(send, 200, {"logged_out": True})

            user, _ = await self._principal(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})

            if method == "GET" and path == "/v1/conversations":
                items = await asyncio.to_thread(
                    self.conversations.list, owner_id=user.user_id, limit=50
                )
                return await self._respond(send, 200, {"conversations": items})

            if method == "POST" and path == "/v1/conversations":
                payload = await self._body(receive)
                if set(payload) - {"title"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                conversation = await asyncio.to_thread(
                    self.conversations.create,
                    owner_id=user.user_id,
                    title=payload.get("title", "New conversation"),
                )
                return await self._respond(send, 201, conversation)

            match = re.fullmatch(r"/v1/conversations/([^/]+)/messages", path)
            if match and not _CONVERSATION_ID.fullmatch(match.group(1)):
                return await self._respond(send, 404, {"error": "not_found"})

            if match and method == "GET":
                query = self._query(scope)
                after = query.get("after_sequence")
                try:
                    after_sequence = int(after) if after is not None else None
                except (TypeError, ValueError):
                    return await self._respond(send, 400, {"error": "invalid_pagination"})
                messages = await asyncio.to_thread(
                    self.conversations.history,
                    owner_id=user.user_id,
                    conversation_id=match.group(1),
                    limit=100,
                    after_sequence=after_sequence,
                )
                return await self._respond(send, 200, {"messages": messages})

            if match and method == "POST":
                payload = await self._body(receive)
                required = {"content", "request_id", "expected_version"}
                if set(payload) != required:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if not isinstance(payload["content"], str) or not isinstance(payload["request_id"], str):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if type(payload["expected_version"]) is not int:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                tools = self.tools_for_user(user)
                if not isinstance(tools, list) or len(tools) > 32:
                    return await self._respond(send, 500, {"error": "tool_configuration_error"})
                result = await asyncio.to_thread(
                    self.conversations.reply,
                    owner_id=user.user_id,
                    conversation_id=match.group(1),
                    user_text=payload["content"],
                    request_id=payload["request_id"],
                    expected_version=payload["expected_version"],
                    tools=tools,
                )
                return await self._respond(send, 200, result)

            return await self._respond(send, 404, {"error": "not_found"})
        except ConversationNotFound:
            return await self._respond(send, 404, {"error": "not_found"})
        except ConversationConflict:
            return await self._respond(send, 409, {"error": "conversation_conflict"})
        except (ValueError, ConversationError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            # Never leak credentials, provider output, SQL, or internal traces.
            return await self._respond(send, 500, {"error": "internal_error"})

    async def _principal(self, scope):
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None, None
        user = await asyncio.to_thread(self.auth.resolve_session, token)
        return user, token if user is not None else None

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
            if len(chunks) > _MAX_BODY_BYTES:
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

    @staticmethod
    def _query(scope):
        from urllib.parse import parse_qs
        raw = scope.get("query_string", b"").decode("ascii", "ignore")
        parsed = parse_qs(raw, keep_blank_values=False, max_num_fields=8)
        return {key: values[-1] for key, values in parsed.items() if values}

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
