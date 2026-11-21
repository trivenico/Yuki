"""Authenticated internal control-plane endpoints for remote Code Mode workers.

These routes are intended to be wrapped around Yuki's existing ASGI API and
called only through an authenticated service binding. They do not expose a
public execution endpoint or create a sandbox themselves.
"""
from __future__ import annotations

import asyncio
import hmac
import json
from typing import Callable, Mapping

from core.auth import BuiltInAuthService
from core.code_execution import CodeExecutionContractError, CodeExecutionManifest
from core.code_execution_tools import AuthenticatedReadToolDispatcher
from core.execution_grants import HMACExecutionGrantAuthority

_MAX_BODY = 96 * 1024


class AuthenticatedRemoteCodeExecutionAPI:
    """Narrow internal RPC bridge to Yuki's native grant and read-tool policy."""

    def __init__(
        self,
        app,
        *,
        auth_service: BuiltInAuthService,
        grant_authority: HMACExecutionGrantAuthority,
        dispatcher: AuthenticatedReadToolDispatcher,
        adapter_token: str,
        audit: Callable[[str, Mapping[str, object]], None],
    ) -> None:
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService")
        if not isinstance(grant_authority, HMACExecutionGrantAuthority):
            raise TypeError("grant_authority must be a HMACExecutionGrantAuthority")
        if not isinstance(dispatcher, AuthenticatedReadToolDispatcher):
            raise TypeError("dispatcher must be an AuthenticatedReadToolDispatcher")
        if not grant_authority.supports_atomic_task_authorization:
            raise ValueError("remote execution requires task-bound atomic authorization")
        if not isinstance(adapter_token, str) or len(adapter_token) < 32 or len(adapter_token) > 512:
            raise ValueError("adapter_token must be a high-entropy bounded secret")
        if not callable(audit):
            raise TypeError("mandatory execution audit callback is required")
        self.app = app
        self.auth = auth_service
        self.grants = grant_authority
        self.dispatcher = dispatcher
        self._adapter_token = adapter_token.encode("utf-8")
        self._audit = audit

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path not in {
            "/v1/internal/executions/authorize",
            "/v1/internal/executions/tool-call",
            "/v1/internal/executions/complete",
        }:
            await self.app(scope, receive, send)
            return
        if scope.get("method", "GET").upper() != "POST":
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        if not headers.get(b"content-type", b"").lower().startswith(b"application/json"):
            return await self._respond(send, 415, {"error": "unsupported_media_type"})
        if not self._authorized_adapter(scope):
            return await self._respond(send, 401, {"error": "adapter_authentication_required"})
        try:
            payload = await self._read_json_body(receive)
            manifest = self._manifest(payload)
            if not set(manifest.tool_names).issubset(self.dispatcher.tool_names):
                raise CodeExecutionContractError("remote manifest contains a tool not registered by Yuki")
            grant = self.grants.resolve_grant(
                payload.get("grant"), manifest, allow_expired=path.endswith("/complete")
            )
            if not path.endswith("/complete"):
                await self._authenticate_manifest(manifest)
            if path.endswith("/authorize"):
                consumed = await asyncio.to_thread(
                    self.grants.consume_execution, grant, manifest
                )
                if consumed is not True:
                    return await self._respond(send, 409, {"authorized": False, "error": "grant_replayed_or_denied"})
                try:
                    await asyncio.to_thread(
                        self._audit,
                        "code_execution.started",
                        self._audit_record(manifest),
                    )
                except Exception:
                    await asyncio.to_thread(
                        self.grants.record_remote_completion,
                        grant,
                        manifest,
                        status="failed",
                        tool_calls=0,
                    )
                    return await self._respond(send, 503, {"authorized": False, "error": "audit_unavailable"})
                return await self._respond(send, 200, {"authorized": True})

            if path.endswith("/tool-call"):
                tool_name = payload.get("tool_name")
                call_index = payload.get("call_index")
                arguments = payload.get("arguments")
                if not isinstance(tool_name, str) or type(call_index) is not int or not isinstance(arguments, dict):
                    return await self._respond(send, 400, {"authorized": False, "error": "invalid_tool_call"})
                record = self._audit_record(manifest)
                def operation():
                    self._audit(
                        "code_execution.tool_requested",
                        {**record, "tool_name": tool_name, "call_index": call_index},
                    )
                    result = self.dispatcher.dispatch(manifest, tool_name, arguments)
                    self._audit(
                        "code_execution.tool_completed",
                        {**record, "tool_name": tool_name, "call_index": call_index},
                    )
                    return result
                result = await asyncio.to_thread(
                    self.grants.authorize_remote_tool_call,
                    grant,
                    manifest,
                    call_index=call_index,
                    tool_name=tool_name,
                    arguments=arguments,
                    operation=operation,
                )
                return await self._respond(send, 200, {"authorized": True, "result": result})

            status = payload.get("status")
            tool_calls = payload.get("tool_calls")
            duration = payload.get("duration_seconds")
            if not isinstance(status, str) or type(tool_calls) is not int:
                return await self._respond(send, 400, {"authorized": False, "error": "invalid_completion"})
            await asyncio.to_thread(
                self.grants.record_remote_completion,
                grant,
                manifest,
                status=status,
                tool_calls=tool_calls,
                duration_seconds=duration,
            )
            await asyncio.to_thread(
                self._audit,
                "code_execution.completed" if status == "succeeded" else "code_execution.failed",
                {**self._audit_record(manifest), "status": status},
            )
            return await self._respond(send, 200, {"authorized": True})
        except CodeExecutionContractError:
            return await self._respond(send, 403, {"authorized": False, "error": "execution_not_authorized"})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return await self._respond(send, 400, {"authorized": False, "error": "invalid_request"})
        except Exception:
            return await self._respond(send, 503, {"authorized": False, "error": "control_plane_unavailable"})

    def _authorized_adapter(self, scope) -> bool:
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        supplied = headers.get(b"x-yuki-adapter-token", b"")
        return hmac.compare_digest(supplied, self._adapter_token)

    async def _authenticate_manifest(self, manifest: CodeExecutionManifest) -> None:
        user = await asyncio.to_thread(
            self.auth.resolve_session_fingerprint,
            manifest.session_id,
        )
        if user is None or user.user_id != manifest.actor_id:
            raise CodeExecutionContractError("remote actor/session binding is no longer active")

    @staticmethod
    def _manifest(payload: dict[str, object]) -> CodeExecutionManifest:
        names = payload.get("tool_names")
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("tool_names must be an array of strings")
        manifest = CodeExecutionManifest(
            execution_id=payload.get("execution_id"),
            actor_id=payload.get("actor_id"),
            session_id=payload.get("session_id"),
            task_id=payload.get("task_id"),
            source_sha256=payload.get("source_sha256"),
            tool_names=tuple(names),
            timeout_seconds=payload.get("timeout_seconds"),
            max_tool_calls=payload.get("max_tool_calls"),
            max_result_bytes=payload.get("max_result_bytes"),
            network_access=payload.get("network_access"),
        )
        manifest.validate()
        if payload.get("manifest_sha256") != manifest.manifest_sha256:
            raise CodeExecutionContractError("remote manifest digest mismatch")
        return manifest

    @staticmethod
    def _audit_record(manifest: CodeExecutionManifest) -> dict[str, object]:
        return {
            "actor_id": manifest.actor_id,
            "task_id": manifest.task_id,
            "execution_id": manifest.execution_id,
            "manifest_sha256": manifest.manifest_sha256,
        }

    @staticmethod
    async def _read_json_body(receive) -> dict[str, object]:
        chunks = bytearray()
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                raise ValueError("client_disconnected")
            if message.get("type") != "http.request":
                raise ValueError("invalid_request_body")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise ValueError("invalid_request_body")
            chunks.extend(chunk)
            if len(chunks) > _MAX_BODY:
                raise ValueError("request_body_too_large")
            if not message.get("more_body", False):
                break

        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate_json_key")
                value[key] = item
            return value

        parsed = json.loads(bytes(chunks).decode("utf-8"), object_pairs_hook=unique_object)
        if not isinstance(parsed, dict):
            raise ValueError("json_object_required")
        return parsed

    @staticmethod
    async def _respond(send, status: int, payload: dict[str, object]):
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
