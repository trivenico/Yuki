"""Authenticated HTTP API for explicitly approved sandbox execution tasks."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from api.request_body import RequestBodyTooLarge, read_json_object
from automation.execution_task import ExecutionApproval, SandboxExecutionTask
from automation.sandbox_execution_submission import SandboxExecutionSubmissionService

_MAX_BODY = 32_768
_GOAL_ID = re.compile(r"^[0-9a-f]{32}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_APPROVAL_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.:-]{0,127}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AuthenticatedSandboxExecutionAPI:
    """Expose only owner-scoped operations; never expose the raw worker."""

    def __init__(self, app: Any, auth_service: Any,
                 submission_service: SandboxExecutionSubmissionService,
                 *, workspace: str, allowed_images: tuple[str, ...]):
        if not isinstance(submission_service, SandboxExecutionSubmissionService):
            raise TypeError("submission_service must be a SandboxExecutionSubmissionService")
        if not isinstance(workspace, str) or not workspace:
            raise ValueError("sandbox workspace is required")
        if not isinstance(allowed_images, tuple) or not allowed_images:
            raise ValueError("sandbox image allow-list is required")
        self.app = app
        self.auth = auth_service
        self.submissions = submission_service
        self.workspace = workspace
        self.allowed_images = frozenset(allowed_images)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        if not path.startswith("/v1/sandbox/"):
            await self.app(scope, receive, send)
            return
        try:
            token = self._bearer(scope)
            if token is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            user = await asyncio.to_thread(self.auth.resolve_session, token)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})

            if path == "/v1/sandbox/goals" and method == "POST":
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                goal_id = await asyncio.to_thread(
                    self.submissions.create_goal, session_token=token
                )
                return await self._respond(send, 201, {"goal_id": goal_id})

            if path == "/v1/sandbox/approvals" and method == "POST":
                payload = await self._body(receive)
                task = self._task(payload)
                if task.image not in self.allowed_images:
                    return await self._respond(send, 400, {"error": "image_not_allowlisted"})
                approval = await asyncio.to_thread(
                    self.submissions.approve_task, task, session_token=token
                )
                return await self._respond(send, 201, {
                    "approval_id": approval.approval_id,
                    "task_id": approval.task_id,
                    "task_fingerprint": approval.task_fingerprint,
                })

            goal_match = re.fullmatch(r"/v1/sandbox/goals/([0-9a-f]{32})/tasks", path)
            if goal_match and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"task", "approval_id"} or not isinstance(payload["task"], dict):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                task = self._task(payload["task"])
                approval_id = payload["approval_id"]
                if not isinstance(approval_id, str) or not _APPROVAL_ID.fullmatch(approval_id):
                    return await self._respond(send, 400, {"error": "invalid_approval"})
                key = self._header(scope, b"idempotency-key")
                if key is None or not _IDEMPOTENCY_KEY.fullmatch(key):
                    return await self._respond(send, 400, {"error": "idempotency_key_required"})
                approval = ExecutionApproval(
                    approval_id=approval_id, actor_id=user.user_id,
                    task_id=task.task_id, task_fingerprint=task.fingerprint(),
                    approved=True,
                )
                queued = await asyncio.to_thread(
                    self.submissions.submit, goal_match.group(1), task, approval,
                    idempotency_key=key, session_token=token,
                )
                return await self._respond(send, 202, {"task": self._summary(queued)})

            goal_list = re.fullmatch(r"/v1/sandbox/goals/([0-9a-f]{32})/tasks", path)
            if goal_list and method == "GET":
                tasks = await asyncio.to_thread(
                    self.submissions.list_tasks, goal_list.group(1), session_token=token
                )
                return await self._respond(send, 200, {
                    "tasks": [self._summary(item) for item in tasks],
                })

            task_match = re.fullmatch(r"/v1/sandbox/tasks/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})", path)
            if task_match and method == "GET":
                task = await asyncio.to_thread(
                    self.submissions.get_task, task_match.group(1), session_token=token
                )
                return await self._respond(send, 200, {"task": self._summary(task)})

            if task_match and method == "DELETE":
                task = await asyncio.to_thread(
                    self.submissions.cancel_task, task_match.group(1), session_token=token
                )
                return await self._respond(send, 200, {"task": self._summary(task)})

            return await self._respond(send, 404, {"error": "not_found"})
        except RequestBodyTooLarge:
            return await self._respond(send, 413, {"error": "request_too_large"})
        except PermissionError:
            return await self._respond(send, 403, {"error": "forbidden"})
        except (ValueError, TypeError, KeyError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except LookupError:
            return await self._respond(send, 404, {"error": "not_found"})

    def _task(self, payload: dict) -> SandboxExecutionTask:
        required = {"image", "argv"}
        optional = {"timeout_seconds", "write_workspace", "max_output_bytes"}
        if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - required - optional:
            raise ValueError("invalid task fields")
        image, argv = payload["image"], payload["argv"]
        if not isinstance(image, str) or image not in self.allowed_images:
            raise ValueError("image is not allow-listed")
        if (not isinstance(argv, list) or not argv or len(argv) > 64
                or any(not isinstance(arg, str) or not arg or len(arg.encode("utf-8")) > 4096 for arg in argv)
                or sum(len(arg.encode("utf-8")) for arg in argv) > 16_384):
            raise ValueError("invalid argv")
        timeout = payload.get("timeout_seconds", 30)
        write = payload.get("write_workspace", False)
        output = payload.get("max_output_bytes", 24_576)
        if type(timeout) is not int or type(write) is not bool or type(output) is not int:
            raise ValueError("invalid task bounds")
        task = SandboxExecutionTask(
            task_id=uuid.uuid4().hex, image=image, argv=tuple(argv),
            workspace=self.workspace, timeout_seconds=timeout,
            write_workspace=write, max_output_bytes=output,
        )
        task.validate()
        return task

    @staticmethod
    def _summary(task):
        return {
            "task_id": task["id"], "goal_id": task["goal_id"],
            "state": task["state"], "task_type": task["task_type"],
            "attempts": task["attempts"], "last_error": task.get("last_error"),
            "created_at": task["created_at"], "updated_at": task["updated_at"],
        }

    @staticmethod
    def _bearer(scope):
        raw = AuthenticatedSandboxExecutionAPI._header(scope, b"authorization")
        if raw is None:
            return None
        scheme, sep, token = raw.partition(" ")
        if not sep or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return token

    @staticmethod
    def _header(scope, name):
        values = [v for k, v in scope.get("headers", [])
                  if isinstance(k, bytes) and isinstance(v, bytes) and k.lower() == name]
        if len(values) != 1:
            return None
        try:
            return values[0].decode("latin-1")
        except UnicodeDecodeError:
            return None

    @staticmethod
    async def _body(receive):
        return await read_json_object(receive, _MAX_BODY)

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
        ]})
        await send({"type": "http.response.body", "body": body})
