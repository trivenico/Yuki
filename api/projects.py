"""Authenticated project, task-membership, milestone, and progress API."""
from __future__ import annotations

import asyncio
import json
import re

from automation.task_runtime import TaskRuntime
from core.auth import BuiltInAuthService
from core.project_registry import ProjectRegistry
from core.project_intelligence import ProjectTaskEvidence, build_project_progress

_PROJECT = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})$")
_PROJECT_TASKS = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/tasks$")
_PROJECT_MILESTONES = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/milestones$")
_MILESTONE_ACTION = re.compile(r"^/v1/projects/milestones/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/complete$")
_MAX_BODY = 12_000


class AuthenticatedProjectAPI:
    """Wrap an ASGI app; derive owner identity exclusively from the auth session."""

    def __init__(self, app, auth_service: BuiltInAuthService, registry: ProjectRegistry, task_runtime: TaskRuntime):
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService.")
        if not isinstance(registry, ProjectRegistry):
            raise TypeError("registry must be a ProjectRegistry.")
        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("task_runtime must be a TaskRuntime.")
        self.app, self.auth, self.registry, self.tasks = app, auth_service, registry, task_runtime

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope.get("method", "GET").upper(), scope.get("path", "")
        if not path.startswith("/v1/projects"):
            await self.app(scope, receive, send)
            return
        user = await self._principal(scope)
        if user is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        try:
            if path == "/v1/projects" and method == "GET":
                projects = await asyncio.to_thread(self.registry.list_projects, owner_id=user.user_id)
                return await self._respond(send, 200, {"projects": [self._project_dict(p) for p in projects]})
            if path == "/v1/projects" and method == "POST":
                payload = await self._body(receive)
                if set(payload) - {"name", "description"} or "name" not in payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                project = await asyncio.to_thread(
                    self.registry.create_project, owner_id=user.user_id,
                    name=payload["name"], description=payload.get("description", ""),
                )
                return await self._respond(send, 201, {"project": self._project_dict(project)})
            match = _PROJECT_TASKS.fullmatch(path)
            if match and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"task_id"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                await asyncio.to_thread(
                    self.registry.add_task, owner_id=user.user_id,
                    project_id=match.group(1), task_id=payload["task_id"],
                )
                return await self._respond(send, 201, {"added": True})
            if match and method == "GET":
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=match.group(1),
                )
                return await self._respond(send, 200, {"task_ids": task_ids})
            if match and method == "DELETE":
                payload = await self._body(receive)
                if set(payload) != {"task_id"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                removed = await asyncio.to_thread(
                    self.registry.remove_task, owner_id=user.user_id,
                    project_id=match.group(1), task_id=payload["task_id"],
                )
                return await self._respond(send, 200, {"removed": removed})
            match = _PROJECT_MILESTONES.fullmatch(path)
            if match and method == "POST":
                payload = await self._body(receive)
                if set(payload) - {"title", "due_at"} or "title" not in payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                milestone = await asyncio.to_thread(
                    self.registry.create_milestone, owner_id=user.user_id,
                    project_id=match.group(1), title=payload["title"], due_at=payload.get("due_at"),
                )
                return await self._respond(send, 201, {"milestone": self._milestone_dict(milestone)})
            if match and method == "GET":
                milestones = await asyncio.to_thread(
                    self.registry.list_milestones, owner_id=user.user_id, project_id=match.group(1),
                )
                return await self._respond(send, 200, {"milestones": [self._milestone_dict(m) for m in milestones]})
            match = _MILESTONE_ACTION.fullmatch(path)
            if match and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"expected_version", "verification_ref"} or type(payload.get("expected_version")) is not int:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                milestone = await asyncio.to_thread(
                    self.registry.complete_milestone, owner_id=user.user_id,
                    milestone_id=match.group(1), expected_version=payload["expected_version"],
                    verification_ref=payload["verification_ref"],
                )
                return await self._respond(send, 200, {"milestone": self._milestone_dict(milestone)})
            project_match = _PROJECT.fullmatch(path)
            if project_match and method == "GET":
                # Project lookup first prevents cross-owner project ID probing.
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=project_match.group(1), limit=500,
                )
                evidence = []
                for task_id in task_ids:
                    task = await asyncio.to_thread(self.tasks.get, actor_id=user.user_id, task_id=task_id)
                    evidence.append(ProjectTaskEvidence(
                        task_id=task.task_id, owner_id=user.user_id, state=task.state,
                        updated_at=task.updated_at, dependencies=tuple(task.dependencies),
                    ))
                report = build_project_progress(
                    project_id=project_match.group(1), owner_id=user.user_id, tasks=evidence,
                )
                return await self._respond(send, 200, {"progress": {
                    "project_id": report.project_id, "total_tasks": report.total_tasks,
                    "counts": report.counts, "verified_completed": report.verified_completed,
                    "succeeded_unverified": report.succeeded_unverified,
                    "measured_progress_percent": report.measured_progress_percent,
                    "stale_task_ids": report.stale_task_ids,
                    "unresolved_dependency_task_ids": report.unresolved_dependency_task_ids,
                    "recommendations": [{
                        "code": item.code, "message": item.message,
                        "evidence_task_ids": item.evidence_task_ids, "confidence": item.confidence,
                    } for item in report.recommendations],
                    "generated_at": report.generated_at,
                }})
            return await self._respond(send, 405 if method not in {"GET", "POST", "DELETE"} else 404,
                                       {"error": "method_not_allowed" if method not in {"GET", "POST", "DELETE"} else "not_found"})
        except PermissionError:
            return await self._respond(send, 403, {"error": "access_denied"})
        except (LookupError, KeyError):
            return await self._respond(send, 404, {"error": "not_found"})
        except (ValueError, TypeError, json.JSONDecodeError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 503, {"error": "project_service_unavailable"})

    async def _principal(self, scope):
        headers = {k.lower(): v for k, v in scope.get("headers", []) if isinstance(k, bytes) and isinstance(v, bytes)}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, sep, token = authorization.partition(" ")
        if not sep or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return await asyncio.to_thread(self.auth.resolve_session, token)

    @staticmethod
    async def _body(receive):
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > _MAX_BODY:
                raise ValueError("request body too large")
            if not message.get("more_body", False):
                break
        if not body:
            return {}
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    @staticmethod
    def _project_dict(project):
        return {"project_id": project.project_id, "name": project.name,
                "description": project.description, "created_at": project.created_at}

    @staticmethod
    def _milestone_dict(milestone):
        return {"milestone_id": milestone.milestone_id, "project_id": milestone.project_id,
                "title": milestone.title, "status": milestone.status, "due_at": milestone.due_at,
                "verification_ref": milestone.verification_ref, "version": milestone.version}

    @staticmethod
    async def _respond(send, status, value):
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
