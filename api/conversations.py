"""Dependency-free ASGI HTTP API for authenticated Yuki conversations.

Account creation/bootstrap is intentionally out of band. The embedding
application constructs auth, provider, guarded tools and conversation services.
No request-supplied owner IDs or tool schemas are trusted.
"""
from __future__ import annotations

import asyncio
import json
import re
import queue
import threading
from typing import Any, Callable

from core.auth import AuthenticationError, BuiltInAuthService
from core.conversation_service import ConversationService
from core.learning import LearningCoordinator
from core.proactive_preferences import ProactiveNotificationPreferences
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
        provider_catalog: Callable[[], list[dict[str, Any]]] | None = None,
        provider_selector: Callable[[str], dict[str, Any]] | None = None,
        learning_for_user: Callable[[Any], LearningCoordinator] | None = None,
        proactive_preferences_for_user: Callable[[Any], ProactiveNotificationPreferences] | None = None,
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
        if provider_catalog is not None and not callable(provider_catalog):
            raise TypeError("provider_catalog must be callable")
        self.provider_catalog = provider_catalog or (lambda: [])
        if provider_selector is not None and not callable(provider_selector):
            raise TypeError("provider_selector must be callable")
        self.provider_selector = provider_selector
        if learning_for_user is not None and not callable(learning_for_user):
            raise TypeError("learning_for_user must be callable")
        self.learning_for_user = learning_for_user
        if proactive_preferences_for_user is not None and not callable(proactive_preferences_for_user):
            raise TypeError('proactive_preferences_for_user must be callable')
        self.proactive_preferences_for_user = proactive_preferences_for_user
        self._active_lock = threading.Lock()
        self._active_generations: dict[tuple[str, str, str], threading.Event] = {}

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

            if method == "GET" and path == "/v1/auth/me":
                return await self._respond(send, 200, {
                    "user": {"user_id": user.user_id, "username": user.username, "role": user.role}
                })

            if path == "/v1/proactive/preferences" and method in {"GET", "PUT"}:
                if self.proactive_preferences_for_user is None:
                    return await self._respond(send, 503, {"error": "proactive_preferences_unavailable"})
                preferences = self.proactive_preferences_for_user(user)
                if not isinstance(preferences, ProactiveNotificationPreferences):
                    return await self._respond(send, 503, {"error": "proactive_preferences_unavailable"})
                if method == "GET":
                    current = await asyncio.to_thread(preferences.get, owner_id=user.user_id)
                    return await self._respond(send, 200, {
                        "notifications_enabled": current["notifications_enabled"],
                        "updated_at": current["updated_at"],
                    })
                payload = await self._body(receive)
                if set(payload) != {"notifications_enabled"} or type(payload.get("notifications_enabled")) is not bool:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                updated = await asyncio.to_thread(
                    preferences.set, owner_id=user.user_id,
                    notifications_enabled=payload["notifications_enabled"],
                )
                return await self._respond(send, 200, {
                    "notifications_enabled": updated["notifications_enabled"],
                    "updated_at": updated["updated_at"],
                })

            if method == "PUT" and path == "/v1/providers/active":
                if user.role != "admin":
                    return await self._respond(send, 403, {"error": "administrator_required"})
                if self.provider_selector is None:
                    return await self._respond(send, 503, {"error": "provider_selection_unavailable"})
                payload = await self._body(receive)
                if (set(payload) != {"provider", "confirm_global", "confirm_cloud"}
                        or not isinstance(payload.get("provider"), str)
                        or payload.get("confirm_global") is not True
                        or not isinstance(payload.get("confirm_cloud"), bool)):
                    return await self._respond(send, 400, {"error": "explicit_confirmation_required"})
                try:
                    target = next(
                        (item for item in self.provider_catalog()
                         if item.get("name") == payload["provider"].strip().lower()),
                        None,
                    )
                    if target is None:
                        return await self._respond(send, 422, {"error": "provider_not_implemented"})
                    if target.get("privacy") != "local_runtime" and not payload["confirm_cloud"]:
                        return await self._respond(send, 400, {"error": "cloud_consent_required"})
                    selected = self.provider_selector(payload["provider"])
                except ValueError as exc:
                    code = str(exc)
                    if code not in {"invalid_provider", "provider_not_implemented", "provider_not_ready"}:
                        code = "provider_selection_rejected"
                    return await self._respond(send, 422, {"error": code})
                except Exception:
                    return await self._respond(send, 503, {"error": "provider_selection_failed"})
                return await self._respond(send, 200, {"provider": {
                    key: selected[key] for key in (
                        "name", "label", "implemented", "ready", "privacy",
                        "configured_model", "credential_configured", "selected", "status",
                    ) if key in selected
                }})

            if method == "GET" and path == "/v1/providers":
                try:
                    providers = self.provider_catalog()
                    if not isinstance(providers, list) or len(providers) > 32:
                        raise ValueError("invalid provider catalog")
                    safe = []
                    for item in providers:
                        if not isinstance(item, dict):
                            raise ValueError("invalid provider catalog entry")
                        safe.append({
                            key: item[key] for key in (
                                "name", "label", "implemented", "ready", "privacy",
                                "configured_model", "credential_configured", "selected", "status",
                            ) if key in item
                        })
                    return await self._respond(send, 200, {"providers": safe})
                except Exception:
                    return await self._respond(send, 500, {"error": "provider_catalog_unavailable"})

            if self.learning_for_user is not None:
                if method == "GET" and path == "/v1/learning/candidates":
                    coordinator = self.learning_for_user(user)
                    if not isinstance(coordinator, LearningCoordinator):
                        return await self._respond(send, 503, {"error": "learning_unavailable"})
                    items = await asyncio.to_thread(
                        coordinator.pending, user.user_id, limit=100,
                    )
                    return await self._respond(send, 200, {
                        "candidates": [self._learning_candidate(item) for item in items]
                    })

                if method == "GET" and path == "/v1/learning/consolidation":
                    coordinator = self.learning_for_user(user)
                    if not isinstance(coordinator, LearningCoordinator):
                        return await self._respond(send, 503, {"error": "learning_unavailable"})
                    try:
                        proposals = await asyncio.to_thread(
                            coordinator.consolidation_proposals, user.user_id, limit=100,
                        )
                    except PermissionError:
                        return await self._respond(send, 403, {"error": "learning_consent_required"})
                    except (TypeError, ValueError):
                        return await self._respond(send, 503, {"error": "consolidation_unavailable"})
                    return await self._respond(send, 200, {
                        "proposals": [item.__dict__ for item in proposals]
                    })

                if method == "POST" and path == "/v1/learning/consolidation/review":
                    payload = await self._body(receive)
                    required = {
                        "keep_id", "archive_id", "keep_version", "archive_version", "approved",
                    }
                    if (
                        set(payload) != required
                        or not isinstance(payload.get("keep_id"), str)
                        or not isinstance(payload.get("archive_id"), str)
                        or type(payload.get("keep_version")) is not int
                        or type(payload.get("archive_version")) is not int
                        or type(payload.get("approved")) is not bool
                    ):
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    coordinator = self.learning_for_user(user)
                    if not isinstance(coordinator, LearningCoordinator):
                        return await self._respond(send, 503, {"error": "learning_unavailable"})
                    try:
                        reviewed = await asyncio.to_thread(
                            coordinator.review_consolidation, user.user_id,
                            keep_id=payload["keep_id"], archive_id=payload["archive_id"],
                            keep_version=payload["keep_version"],
                            archive_version=payload["archive_version"],
                            approved=payload["approved"],
                        )
                    except PermissionError:
                        return await self._respond(send, 403, {"error": "learning_consent_required"})
                    except (TypeError, ValueError):
                        return await self._respond(send, 409, {"error": "consolidation_suggestion_stale"})
                    except Exception:
                        return await self._respond(send, 503, {"error": "consolidation_review_failed"})
                    return await self._respond(send, 200, {"reviewed": reviewed})

                if method == "POST" and path == "/v1/learning/feedback":
                    payload = await self._body(receive)
                    if set(payload) != {"lesson", "evidence"}:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    coordinator = self.learning_for_user(user)
                    if not isinstance(coordinator, LearningCoordinator):
                        return await self._respond(send, 503, {"error": "learning_unavailable"})
                    try:
                        candidate = await asyncio.to_thread(
                            coordinator.propose_foreground_feedback,
                            user.user_id, lesson=payload["lesson"], evidence=payload["evidence"],
                        )
                    except PermissionError:
                        return await self._respond(send, 403, {"error": "learning_consent_required"})
                    except (TypeError, ValueError):
                        return await self._respond(send, 400, {"error": "invalid_learning_feedback"})
                    except RuntimeError:
                        return await self._respond(send, 429, {"error": "learning_queue_full"})
                    return await self._respond(send, 201, {
                        "candidate": self._learning_candidate(candidate)
                    })

                review_match = re.fullmatch(r"/v1/learning/candidates/([a-f0-9]{32})/review", path)
                if review_match and method == "POST":
                    payload = await self._body(receive)
                    if set(payload) != {"approved"} or type(payload.get("approved")) is not bool:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    coordinator = self.learning_for_user(user)
                    if not isinstance(coordinator, LearningCoordinator):
                        return await self._respond(send, 503, {"error": "learning_unavailable"})
                    try:
                        reviewed = await asyncio.to_thread(
                            coordinator.review, user.user_id, review_match.group(1),
                            approved=payload["approved"],
                        )
                    except PermissionError:
                        return await self._respond(send, 403, {"error": "learning_consent_required"})
                    except (TypeError, ValueError):
                        return await self._respond(send, 400, {"error": "invalid_learning_review"})
                    except Exception:
                        return await self._respond(send, 503, {"error": "learning_review_failed"})
                    return await self._respond(send, 200, {"reviewed": reviewed})

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


            cancel_match = re.fullmatch(r"/v1/conversations/([^/]+)/messages/cancel", path)
            if cancel_match and method == "POST":
                conversation_id = cancel_match.group(1)
                if not _CONVERSATION_ID.fullmatch(conversation_id):
                    return await self._respond(send, 404, {"error": "not_found"})
                payload = await self._body(receive)
                if set(payload) != {"request_id"} or not isinstance(payload.get("request_id"), str) or not payload["request_id"].strip() or len(payload["request_id"]) > 160:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                # Ownership is checked before cancellation; callers cannot cancel
                # another user's generation by guessing a conversation or request ID.
                await asyncio.to_thread(
                    self.conversations.store.get_conversation,
                    owner_id=user.user_id, conversation_id=conversation_id,
                )
                key = (user.user_id, conversation_id, payload["request_id"])
                with self._active_lock:
                    cancellation = self._active_generations.get(key)
                    if cancellation is not None:
                        cancellation.set()
                return await self._respond(send, 200, {"cancel_requested": cancellation is not None})

            match = re.fullmatch(r"/v1/conversations/([^/]+)/messages", path)
            if match and not _CONVERSATION_ID.fullmatch(match.group(1)):
                return await self._respond(send, 404, {"error": "not_found"})

            stream_match = re.fullmatch(r"/v1/conversations/([^/]+)/messages/stream", path)
            if stream_match and method == "POST":
                conversation_id = stream_match.group(1)
                if not _CONVERSATION_ID.fullmatch(conversation_id):
                    return await self._respond(send, 404, {"error": "not_found"})
                payload = await self._body(receive)
                required = {"content", "request_id", "expected_version"}
                if set(payload) != required or not isinstance(payload.get("content"), str) or not isinstance(payload.get("request_id"), str) or type(payload.get("expected_version")) is not int:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                tools = self.tools_for_user(user)
                if not isinstance(tools, list) or len(tools) > 32:
                    return await self._respond(send, 500, {"error": "tool_configuration_error"})
                return await self._stream_reply(
                    send, receive=receive, owner_id=user.user_id, conversation_id=conversation_id,
                    payload=payload, tools=tools,
                )

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

    @staticmethod
    def _learning_candidate(candidate):
        return {
            "candidate_id": candidate.candidate_id,
            "source": candidate.source,
            "lesson": candidate.lesson,
            "evidence": candidate.evidence,
            "created_at": candidate.created_at,
        }

    async def _stream_reply(self, send, *, receive, owner_id, conversation_id, payload, tools):
        """Run synchronous generation off-loop and emit bounded SSE events."""
        loop = asyncio.get_running_loop()
        events = asyncio.Queue(maxsize=64)
        cancellation = threading.Event()
        active_key = (owner_id, conversation_id, payload["request_id"])
        with self._active_lock:
            if active_key in self._active_generations:
                return await self._respond(send, 409, {"error": "generation_already_active"})
            self._active_generations[active_key] = cancellation
        sentinel = object()

        def publish(event):
            def enqueue():
                if events.full():
                    # A slow client must not stall provider generation forever.
                    while not events.empty():
                        try:
                            events.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    events.put_nowait({"type": "error", "error": "stream_consumer_too_slow"})
                else:
                    events.put_nowait(event)
            loop.call_soon_threadsafe(enqueue)

        def run():
            try:
                result = self.conversations.reply(
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    user_text=payload["content"],
                    request_id=payload["request_id"],
                    expected_version=payload["expected_version"],
                    tools=tools,
                    on_text_delta=lambda text: publish({"type": "delta", "text": text}),
                    cancellation_requested=cancellation.is_set,
                )
                publish({"type": "cancelled" if result.get("status") == "cancelled" else "completed", "result": result})
            except ConversationNotFound:
                publish({"type": "error", "error": "not_found"})
            except ConversationConflict:
                publish({"type": "error", "error": "conversation_conflict"})
            except Exception:
                publish({"type": "error", "error": "internal_error"})
            finally:
                with self._active_lock:
                    self._active_generations.pop(active_key, None)
                loop.call_soon_threadsafe(events.put_nowait, sentinel)

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache, no-store"),
                (b"x-content-type-options", b"nosniff"),
                (b"x-accel-buffering", b"no"),
            ],
        })
        worker = asyncio.create_task(asyncio.to_thread(run))
        async def watch_disconnect():
            while True:
                incoming = await receive()
                if incoming.get("type") == "http.disconnect":
                    cancellation.set()
                    return
        disconnect_watcher = asyncio.create_task(watch_disconnect())
        try:
            while True:
                event = await events.get()
                if event is sentinel:
                    break
                frame = ("event: " + event["type"] + "\n" +
                         "data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n")
                await send({"type": "http.response.body", "body": frame.encode("utf-8"), "more_body": True})
            await worker
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception:
            cancellation.set()
            # A disconnected client must not turn the worker into an unobserved task.
            await worker
            raise
        finally:
            disconnect_watcher.cancel()
            try:
                await disconnect_watcher
            except asyncio.CancelledError:
                pass

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
