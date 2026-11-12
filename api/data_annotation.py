"""Authenticated API for Yuki's owner-scoped data annotation workbench.

These routes manage local projects and human-reviewed annotations only. They
never submit annotations to a marketplace or client system.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import re

from core.data_annotation import (
    AnnotationConflict,
    SQLiteAnnotationWorkbench,
)


_MAX_BODY = 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")
_PROJECT_FIELDS = frozenset({
    "name", "task_type", "instructions", "rubric", "ai_assistance_mode",
})
_TASK_FIELDS = frozenset({"external_ref", "asset_ref", "input_data"})
_SUBMIT_FIELDS = frozenset({
    "payload", "expected_version", "ai_assisted", "human_confirmed",
})


class AuthenticatedAnnotationAPI:
    """Authenticate every request and derive owner identity only from its token."""

    def __init__(self, app, auth_service, workbench: SQLiteAnnotationWorkbench):
        if not isinstance(workbench, SQLiteAnnotationWorkbench):
            raise TypeError("workbench_must_be_SQLiteAnnotationWorkbench")
        self.app = app
        self.auth = auth_service
        self.workbench = workbench

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        prefix = "/v1/annotation"
        path = scope.get("path", "/")
        if path != prefix and not path.startswith(prefix + "/"):
            await self.app(scope, receive, send)
            return
        try:
            user = await self._user(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            method = scope.get("method", "GET").upper()
            if path == prefix + "/projects":
                if method == "POST":
                    payload = await self._body(receive)
                    if not {"name", "task_type", "instructions", "rubric"} <= set(payload):
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    if set(payload) - _PROJECT_FIELDS:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    project = await asyncio.to_thread(
                        self.workbench.create_project, user.user_id, **payload
                    )
                    return await self._respond(send, 201, {"project": asdict(project)})
                if method == "GET":
                    projects = await asyncio.to_thread(
                        self.workbench.list_projects, user.user_id
                    )
                    return await self._respond(send, 200, {
                        "projects": [asdict(item) for item in projects],
                    })
                return await self._respond(send, 405, {"error": "method_not_allowed"})

            match = re.fullmatch(re.escape(prefix) + r"/projects/([0-9a-f]{32})", path)
            if match and method == "GET":
                project = await asyncio.to_thread(
                    self.workbench.get_project, user.user_id, match.group(1)
                )
                return await self._respond(
                    send, 200 if project else 404,
                    {"project": asdict(project)} if project else {"error": "not_found"},
                )

            match = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/tasks/batch", path
            )
            if match and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"tasks"} or not isinstance(payload["tasks"], list):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                tasks = await asyncio.to_thread(
                    self.workbench.create_tasks, user.user_id, match.group(1),
                    tasks=payload["tasks"],
                )
                return await self._respond(send, 201, {
                    "tasks": [asdict(item) for item in tasks],
                })

            match = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/export", path
            )
            if match and method == "GET":
                records = await asyncio.to_thread(
                    self.workbench.export_accepted, user.user_id, match.group(1)
                )
                return await self._respond(send, 200, {
                    "format": "json",
                    "accepted_annotations": records,
                })

            match = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/tasks", path
            )
            if match:
                project_id = match.group(1)
                if method == "POST":
                    payload = await self._body(receive)
                    if not {"external_ref", "input_data"} <= set(payload):
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    if set(payload) - _TASK_FIELDS:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    task = await asyncio.to_thread(
                        self.workbench.create_task, user.user_id, project_id, **payload
                    )
                    return await self._respond(send, 201, {"task": asdict(task)})
                if method == "GET":
                    tasks = await asyncio.to_thread(
                        self.workbench.list_tasks, user.user_id, project_id
                    )
                    return await self._respond(send, 200, {
                        "tasks": [asdict(item) for item in tasks],
                    })
                return await self._respond(send, 405, {"error": "method_not_allowed"})

            match = re.fullmatch(
                re.escape(prefix) + r"/tasks/([0-9a-f]{32})/(claim|submit|submissions)",
                path,
            )
            if match:
                task_id, action = match.groups()
                if action == "claim" and method == "POST":
                    payload = await self._body(receive)
                    if set(payload) != {"expected_version"}:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    task = await asyncio.to_thread(
                        self.workbench.claim_task, user.user_id, task_id,
                        expected_version=payload["expected_version"],
                    )
                    return await self._respond(
                        send, 200 if task else 404,
                        {"task": asdict(task)} if task else {"error": "not_found"},
                    )
                if action == "submit" and method == "POST":
                    payload = await self._body(receive)
                    if not {"payload", "expected_version", "human_confirmed"} <= set(payload):
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    if set(payload) - _SUBMIT_FIELDS:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    submission = await asyncio.to_thread(
                        self.workbench.submit, user.user_id, task_id, **payload
                    )
                    return await self._respond(
                        send, 201 if submission else 404,
                        {"submission": asdict(submission)} if submission else {"error": "not_found"},
                    )
                if action == "submissions" and method == "GET":
                    items = await asyncio.to_thread(
                        self.workbench.list_submissions, user.user_id, task_id
                    )
                    return await self._respond(send, 200, {
                        "submissions": [asdict(item) for item in items],
                    })
                return await self._respond(send, 405, {"error": "method_not_allowed"})

            match = re.fullmatch(
                re.escape(prefix) + r"/submissions/([0-9a-f]{32})/review", path
            )
            if match and method == "POST":
                payload = await self._body(receive)
                if set(payload) - {"decision", "notes"} or "decision" not in payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                submission = await asyncio.to_thread(
                    self.workbench.review, user.user_id, match.group(1), **payload
                )
                return await self._respond(
                    send, 200 if submission else 404,
                    {"submission": asdict(submission)} if submission else {"error": "not_found"},
                )
            return await self._respond(send, 404, {"error": "not_found"})
        except AnnotationConflict:
            return await self._respond(send, 409, {"error": "version_conflict"})
        except PermissionError:
            return await self._respond(send, 403, {"error": "human_confirmation_required"})
        except LookupError:
            return await self._respond(send, 404, {"error": "not_found"})
        except (ValueError, TypeError, UnicodeDecodeError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 500, {"error": "internal_error"})

    async def _user(self, scope):
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        raw = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = raw.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return await asyncio.to_thread(self.auth.resolve_session, token)

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
        payload = json.loads(chunks.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request_must_be_object")
        return payload

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
