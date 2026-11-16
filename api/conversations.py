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
from api.request_body import RequestBodyTooLarge, read_json_object
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.approved_tool_action_executor import ApprovedToolActionExecutor
from core.tool_action_reconciliation import ToolActionReconciliationService
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
        tool_action_approvals: SQLiteToolActionApprovalStore | None = None,
        pending_tool_action_proposals: SQLitePendingToolActionProposalStore | None = None,
        approved_tool_action_executor: ApprovedToolActionExecutor | None = None,
        tool_action_reconciler: ToolActionReconciliationService | None = None,
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
        if tool_action_approvals is not None and not isinstance(
            tool_action_approvals, SQLiteToolActionApprovalStore
        ):
            raise TypeError("tool_action_approvals must be a SQLiteToolActionApprovalStore")
        self.tool_action_approvals = tool_action_approvals
        if pending_tool_action_proposals is not None and not isinstance(
            pending_tool_action_proposals, SQLitePendingToolActionProposalStore
        ):
            raise TypeError("pending_tool_action_proposals must be a SQLitePendingToolActionProposalStore")
        self.pending_tool_action_proposals = pending_tool_action_proposals
        if approved_tool_action_executor is not None:
            if not isinstance(approved_tool_action_executor, ApprovedToolActionExecutor):
                raise TypeError("approved_tool_action_executor must be an ApprovedToolActionExecutor")
            if pending_tool_action_proposals is None or approved_tool_action_executor.proposals is not pending_tool_action_proposals:
                raise ValueError("executor must use the API's pending proposal store")
            if tool_action_approvals is None or approved_tool_action_executor.approvals is not tool_action_approvals:
                raise ValueError("executor must use the API's approval store")
        self.approved_tool_action_executor = approved_tool_action_executor
        if tool_action_reconciler is not None:
            if not isinstance(tool_action_reconciler, ToolActionReconciliationService):
                raise TypeError("tool_action_reconciler must be a ToolActionReconciliationService")
            if pending_tool_action_proposals is None or tool_action_reconciler.proposals is not pending_tool_action_proposals:
                raise ValueError("reconciler must use the API's pending proposal store")
        self.tool_action_reconciler = tool_action_reconciler
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

            if path == "/v1/tool-action-proposals" and method == "GET":
                if self.pending_tool_action_proposals is None:
                    return await self._respond(send, 503, {"error": "proposal_management_unavailable"})
                query = self._query(scope)
                if set(query) - {"limit", "category"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    limit = int(query.get("limit", "50"))
                    proposals = await asyncio.to_thread(
                        self.pending_tool_action_proposals.list_pending,
                        principal_id=user.user_id, limit=limit,
                    )
                except (TypeError, ValueError):
                    return await self._respond(send, 400, {"error": "invalid_limit"})
                return await self._respond(send, 200, {
                    "proposals": [self._proposal_view(item) for item in proposals]
                })

            recover_match = re.fullmatch(
                r"/v1/tool-action-proposals/([A-Za-z0-9_-]{1,128})/recover", path,
            )
            if recover_match and method == "POST":
                if self.approved_tool_action_executor is None:
                    return await self._respond(send, 503, {"error": "proposal_execution_unavailable"})
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    recovered = await asyncio.to_thread(
                        self.approved_tool_action_executor.recover_interrupted_execution,
                        recover_match.group(1), principal_id=user.user_id,
                    )
                except LookupError:
                    return await self._respond(send, 404, {"error": "not_found"})
                return await self._respond(send, 200, {
                    "proposal_id": recovered.proposal_id,
                    "execution_state": recovered.state,
                    "result": recovered.result,
                })

            reconcile_match = re.fullmatch(
                r"/v1/tool-action-proposals/([A-Za-z0-9_-]{1,128})/reconcile", path,
            )
            if reconcile_match and method == "POST":
                if self.tool_action_reconciler is None:
                    return await self._respond(send, 503, {"error": "proposal_reconciliation_unavailable"})
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    action = await asyncio.to_thread(
                        self.tool_action_reconciler.reconcile_proposal,
                        reconcile_match.group(1), principal_id=user.user_id,
                    )
                except LookupError:
                    return await self._respond(send, 404, {"error": "not_found"})
                except ValueError:
                    return await self._respond(send, 409, {"error": "proposal_not_reconcilable"})
                except Exception:
                    return await self._respond(send, 503, {"error": "reconciliation_failed"})
                return await self._respond(send, 200, {
                    "action_id": action.action_id,
                    "state": action.state,
                    "error_code": action.error_code,
                    "result": action.result,
                })

            execute_match = re.fullmatch(
                r"/v1/tool-action-proposals/([A-Za-z0-9_-]{1,128})/execute", path,
            )
            if execute_match and method == "POST":
                if self.approved_tool_action_executor is None:
                    return await self._respond(send, 503, {"error": "proposal_execution_unavailable"})
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                proposal_id = execute_match.group(1)
                try:
                    execution = await asyncio.to_thread(
                        self.approved_tool_action_executor.execute,
                        proposal_id, principal_id=user.user_id,
                    )
                except LookupError:
                    return await self._respond(send, 404, {"error": "not_found"})
                except ValueError:
                    return await self._respond(send, 409, {"error": "proposal_not_executable"})
                except Exception:
                    return await self._respond(send, 503, {"error": "proposal_execution_failed"})
                return await self._respond(send, 200, {
                    "proposal_id": execution.proposal_id,
                    "execution_state": execution.state,
                    "result": execution.result,
                })

            proposal_match = re.fullmatch(
                r"/v1/tool-action-proposals/([A-Za-z0-9_-]{1,128})(?:/(approve|reject))?", path,
            )
            if proposal_match and method in {"GET", "POST"}:
                if self.pending_tool_action_proposals is None:
                    return await self._respond(send, 503, {"error": "proposal_management_unavailable"})
                proposal_id, operation = proposal_match.groups()
                if method == "GET" and operation is not None:
                    return await self._respond(send, 405, {"error": "method_not_allowed"})
                if method == "POST" and operation is None:
                    return await self._respond(send, 405, {"error": "method_not_allowed"})
                if method == "GET":
                    proposal = await asyncio.to_thread(
                        self.pending_tool_action_proposals.get,
                        proposal_id, principal_id=user.user_id,
                    )
                    if proposal is None:
                        return await self._respond(send, 404, {"error": "not_found"})
                    return await self._respond(send, 200, {
                        "proposal": self._proposal_view(proposal)
                    })
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                existing = await asyncio.to_thread(
                    self.pending_tool_action_proposals.get,
                    proposal_id, principal_id=user.user_id,
                )
                if existing is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                if existing.status != "pending":
                    return await self._respond(send, 409, {"error": "proposal_not_pending"})
                decided = await asyncio.to_thread(
                    self.pending_tool_action_proposals.decide,
                    proposal_id, principal_id=user.user_id,
                    decision="approved" if operation == "approve" else "rejected",
                )
                if decided is None:
                    return await self._respond(send, 409, {"error": "proposal_not_pending"})
                return await self._respond(send, 200, {
                    "proposal": self._proposal_view(decided),
                    "execution": "not_started",
                })

            if path == "/v1/tool-approvals/audit" and method == "GET":
                if self.tool_action_approvals is None:
                    return await self._respond(send, 503, {"error": "approval_management_unavailable"})
                query = self._query(scope)
                if set(query) - {"limit"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    limit = int(query.get("limit", "100"))
                    events = await asyncio.to_thread(
                        self.tool_action_approvals.audit_events,
                        principal_id=user.user_id, limit=limit,
                    )
                except (TypeError, ValueError):
                    return await self._respond(send, 400, {"error": "invalid_limit"})
                return await self._respond(send, 200, {"events": events})

            if path == "/v1/tool-approvals/revoke" and method == "POST":
                if self.tool_action_approvals is None:
                    return await self._respond(send, 503, {"error": "approval_management_unavailable"})
                payload = await self._body(receive)
                token = payload.get("approval_token")
                if set(payload) != {"approval_token"} or not isinstance(token, str) or not token or len(token) > 128:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                revoked = await asyncio.to_thread(
                    self.tool_action_approvals.revoke, token, principal_id=user.user_id,
                )
                return await self._respond(send, 200, {"revoked": revoked})

            if method == "GET" and path == "/v1/auth/me":
                return await self._respond(send, 200, {
                    "user": {"user_id": user.user_id, "username": user.username, "role": user.role}
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

            if path == "/v1/memories" and method == "GET":
                query = self._query(scope)
                if set(query) - {"limit", "category"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if query.get("category") not in (None, "note", "preference", "lesson"):
                    return await self._respond(send, 400, {"error": "invalid_category"})
                try:
                    limit = int(query.get("limit", "50"))
                    memories = await asyncio.to_thread(
                        self.conversations.memories, owner_id=user.user_id,
                        limit=limit, category=query.get("category"),
                    )
                except RuntimeError:
                    return await self._respond(send, 503, {"error": "memory_unavailable"})
                except (TypeError, ValueError):
                    return await self._respond(send, 400, {"error": "invalid_limit"})
                return await self._respond(send, 200, {"memories": memories})

            if path == "/v1/memories" and method == "POST":
                payload = await self._body(receive)
                if set(payload) - {"content", "category", "expires_in_seconds"} or "content" not in payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    memory = await asyncio.to_thread(
                        self.conversations.remember, owner_id=user.user_id,
                        content=payload["content"], category=payload.get("category", "note"),
                        expires_in_seconds=payload.get("expires_in_seconds"),
                    )
                except RuntimeError:
                    return await self._respond(send, 503, {"error": "memory_unavailable"})
                except (TypeError, ValueError):
                    return await self._respond(send, 400, {"error": "invalid_memory"})
                return await self._respond(send, 201, {"memory": memory})

            if path == "/v1/memories" and method == "DELETE":
                payload = await self._body(receive)
                if payload != {"confirm": True}:
                    return await self._respond(send, 400, {"error": "explicit_confirmation_required"})
                try:
                    deleted = await asyncio.to_thread(
                        self.conversations.clear_memories, owner_id=user.user_id,
                    )
                except RuntimeError:
                    return await self._respond(send, 503, {"error": "memory_unavailable"})
                return await self._respond(send, 200, {"deleted": deleted})

            memory_match = re.fullmatch(r"/v1/memories/([A-Za-z0-9_-]{1,128})", path)
            if memory_match and method == "DELETE":
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    deleted = await asyncio.to_thread(
                        self.conversations.forget_memory,
                        owner_id=user.user_id, memory_id=memory_match.group(1),
                    )
                except RuntimeError:
                    return await self._respond(send, 503, {"error": "memory_unavailable"})
                return await self._respond(send, 200, {"deleted": deleted})
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
        except RequestBodyTooLarge:
            return await self._respond(send, 413, {"error": "request_body_too_large"})
        except (ValueError, ConversationError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            # Never leak credentials, provider output, SQL, or internal traces.
            return await self._respond(send, 500, {"error": "internal_error"})

    @staticmethod
    def _proposal_view(proposal):
        """Project safe proposal fields; never expose bearer approval tokens."""
        return {
            "proposal_id": proposal.proposal_id,
            "action_id": proposal.action_id,
            "tool_name": proposal.tool_name,
            "arguments": proposal.arguments,
            "request_digest": proposal.request_digest,
            "status": proposal.status,
            "created_at": proposal.created_at,
            "expires_at": proposal.expires_at,
            "decided_at": proposal.decided_at,
            "execution_state": proposal.execution_state,
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
        return await read_json_object(receive, _MAX_BODY_BYTES)

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
