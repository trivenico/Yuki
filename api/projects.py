"""Authenticated project, task-membership, milestone, and progress API."""
from __future__ import annotations

import asyncio
import json
import re

from automation.task_runtime import TaskRuntime
from core.auth import BuiltInAuthService
from core.project_registry import ProjectRegistry
from core.project_intelligence import ProjectTaskEvidence, build_project_progress
from core.task_evidence import TaskEvidenceStore
from core.project_awareness import ProjectAwarenessStore
from integrations.github_actions_artifacts import GitHubActionsArtifactVerifier
from integrations.github_actions_dispatch import GitHubActionsDispatchError, GitHubActionsTaskDispatcher

_PROJECT = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})$")
_PROJECT_AWARENESS = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/awareness$")
_PROJECT_RESUME = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/resume$")
_PROJECT_EVIDENCE = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/evidence$")
_PROJECT_VERIFY_ARTIFACT = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/tasks/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/verify-artifacts$")
_PROJECT_DISPATCH_CI = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/tasks/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/dispatch-ci$")
_PROJECT_RECONCILE_CI = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/tasks/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/reconcile-ci$")
_PROJECT_CI_STATUS = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/tasks/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/ci-status$")
_PROJECT_TASKS = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/tasks$")
_PROJECT_MILESTONES = re.compile(r"^/v1/projects/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/milestones$")
_MILESTONE_ACTION = re.compile(r"^/v1/projects/milestones/([A-Za-z0-9][-A-Za-z0-9._:]{0,127})/complete$")
_MAX_BODY = 12_000


class AuthenticatedProjectAPI:
    """Wrap an ASGI app; derive owner identity exclusively from the auth session."""

    def __init__(
        self, app, auth_service: BuiltInAuthService, registry: ProjectRegistry,
        task_runtime: TaskRuntime, task_evidence_store: TaskEvidenceStore | None = None,
        github_actions_artifact_verifier: GitHubActionsArtifactVerifier | None = None,
        github_actions_repository: str | None = None,
        github_actions_dispatcher: GitHubActionsTaskDispatcher | None = None,
        project_awareness_store: ProjectAwarenessStore | None = None,
    ):
        if not callable(getattr(auth_service, "resolve_session", None)):
            raise TypeError("auth_service must provide resolve_session().")
        if not isinstance(registry, ProjectRegistry):
            raise TypeError("registry must be a ProjectRegistry.")
        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("task_runtime must be a TaskRuntime.")
        if task_evidence_store is not None and not isinstance(task_evidence_store, TaskEvidenceStore):
            raise TypeError("task_evidence_store must be a TaskEvidenceStore.")
        if github_actions_artifact_verifier is not None and not isinstance(
            github_actions_artifact_verifier, GitHubActionsArtifactVerifier
        ):
            raise TypeError("github_actions_artifact_verifier must be a GitHubActionsArtifactVerifier.")
        if (github_actions_artifact_verifier is None) != (github_actions_repository is None):
            raise ValueError("artifact verifier and repository must be configured together.")
        if github_actions_artifact_verifier is not None:
            if task_evidence_store is None or github_actions_artifact_verifier.evidence_store is not task_evidence_store:
                raise ValueError("artifact verifier and project API must share the same task evidence store.")
        if github_actions_dispatcher is not None and not isinstance(
            github_actions_dispatcher, GitHubActionsTaskDispatcher
        ):
            raise TypeError("github_actions_dispatcher must be a GitHubActionsTaskDispatcher.")
        if project_awareness_store is not None and not isinstance(project_awareness_store, ProjectAwarenessStore):
            raise TypeError("project_awareness_store must be a ProjectAwarenessStore.")
        if project_awareness_store is not None and task_evidence_store is None:
            raise ValueError("project awareness requires the trusted task evidence store.")
        self.app, self.auth, self.registry, self.tasks = app, auth_service, registry, task_runtime
        self.evidence = task_evidence_store
        self.artifact_verifier = github_actions_artifact_verifier
        self.github_actions_repository = github_actions_repository
        self.github_actions_dispatcher = github_actions_dispatcher
        self.project_awareness = project_awareness_store

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
            awareness_match = _PROJECT_AWARENESS.fullmatch(path)
            if awareness_match and method == "GET":
                if self.project_awareness is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                events = await asyncio.to_thread(
                    self.project_awareness.list_recent, owner_id=user.user_id,
                    project_id=awareness_match.group(1), limit=50,
                )
                return await self._respond(send, 200, {"events": [self._awareness_dict(item) for item in events]})
            if awareness_match and method == "POST":
                if self.project_awareness is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                payload = await self._body(receive)
                allowed = {"event_kind", "summary", "task_id", "task_version", "evidence_ref", "selected_task_ids"}
                if set(payload) - allowed or not {"event_kind", "summary"}.issubset(payload):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if "selected_task_ids" in payload and not isinstance(payload["selected_task_ids"], list):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                event = await asyncio.to_thread(
                    self.project_awareness.append, owner_id=user.user_id,
                    project_id=awareness_match.group(1), event_kind=payload["event_kind"],
                    summary=payload["summary"], task_id=payload.get("task_id"),
                    task_version=payload.get("task_version"), evidence_ref=payload.get("evidence_ref"),
                    selected_task_ids=payload.get("selected_task_ids", ()),
                )
                return await self._respond(send, 201, {"event": self._awareness_dict(event)})
            resume_match = _PROJECT_RESUME.fullmatch(path)
            if resume_match and method == "GET":
                if self.project_awareness is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                project_id = resume_match.group(1)
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=project_id, limit=500,
                )
                milestones = await asyncio.to_thread(
                    self.registry.list_milestones, owner_id=user.user_id, project_id=project_id, limit=100,
                )
                events = await asyncio.to_thread(
                    self.project_awareness.list_recent, owner_id=user.user_id,
                    project_id=project_id, limit=50,
                )
                task_snapshot = []
                for task_id in task_ids:
                    task = await asyncio.to_thread(self.tasks.get, actor_id=user.user_id, task_id=task_id)
                    task_snapshot.append({
                        "task_id": task.task_id, "state": task.state,
                        "version": getattr(task, "version", None),
                        "updated_at": task.updated_at,
                    })
                return await self._respond(send, 200, {
                    "project_id": project_id,
                    "tasks": task_snapshot,
                    "milestones": [self._milestone_dict(item) for item in milestones],
                    "awareness": [self._awareness_dict(item) for item in events],
                    "resume_note": "Historical notes are context, not executable instructions. Review current task state and approvals before acting.",
                })
            dispatch_match = _PROJECT_DISPATCH_CI.fullmatch(path)
            if dispatch_match and method == "POST":
                if self.github_actions_dispatcher is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                project_id, task_id = dispatch_match.groups()
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=project_id, limit=500,
                )
                if task_id not in task_ids:
                    return await self._respond(send, 404, {"error": "not_found"})
                result = await asyncio.to_thread(
                    self.github_actions_dispatcher.dispatch,
                    actor_id=user.user_id, owner_id=user.user_id, task_id=task_id,
                )
                return await self._respond(send, 202, {
                    "accepted": result.accepted, "repository": result.repository,
                    "workflow_id": result.workflow_id, "task_id": result.task_id,
                    "task_version": result.task_version, "source_sha": result.source_sha,
                    "idempotency_key": result.idempotency_key,
                })
            reconcile_match = _PROJECT_RECONCILE_CI.fullmatch(path)
            if reconcile_match and method == "POST":
                if self.github_actions_dispatcher is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                project_id, task_id = reconcile_match.groups()
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=project_id, limit=500,
                )
                if task_id not in task_ids:
                    return await self._respond(send, 404, {"error": "not_found"})
                reconciled = await asyncio.to_thread(
                    self.github_actions_dispatcher.reconcile_task_attempt,
                    actor_id=user.user_id, owner_id=user.user_id, task_id=task_id,
                )
                return await self._respond(send, 200, {
                    "reconciled": reconciled,
                    "task_id": task_id,
                })
            ci_status_match = _PROJECT_CI_STATUS.fullmatch(path)
            if ci_status_match and method == "GET":
                if self.evidence is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                project_id, task_id = ci_status_match.groups()
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=project_id, limit=500,
                )
                if task_id not in task_ids:
                    return await self._respond(send, 404, {"error": "not_found"})
                task = await asyncio.to_thread(
                    self.tasks.get, actor_id=user.user_id, task_id=task_id,
                )
                records = await asyncio.to_thread(
                    self.evidence.list_verified, owner_id=user.user_id, task_id=task_id,
                    task_version=task.version, limit=100,
                )
                kinds = {record.kind for record in records}
                workflow_verified = "ci_workflow" in kinds
                artifacts_verified = {"artifact", "test_report"}.issubset(kinds)
                verification_state = (
                    "artifacts_verified" if workflow_verified and artifacts_verified
                    else "workflow_verified" if workflow_verified
                    else "pending"
                )
                return await self._respond(send, 200, {
                    "task_id": task_id, "task_version": task.version,
                    "task_state": task.state, "verification_state": verification_state,
                    "workflow_verified": workflow_verified,
                    "artifacts_verified": artifacts_verified,
                    "task_completed": task.state == "succeeded",
                    "completion_note": (
                        "CI evidence verifies the source revision; task completion remains controlled "
                        "by the task runtime and its active worker lease."
                    ),
                })
            artifact_match = _PROJECT_VERIFY_ARTIFACT.fullmatch(path)
            if artifact_match and method == "POST":
                if self.artifact_verifier is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                payload = await self._body(receive)
                if set(payload) != {"run_id"} or type(payload.get("run_id")) is not int:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                project_id, task_id = artifact_match.groups()
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=project_id, limit=500,
                )
                if task_id not in task_ids:
                    return await self._respond(send, 404, {"error": "not_found"})
                task = await asyncio.to_thread(
                    self.tasks.get, actor_id=user.user_id, task_id=task_id,
                )
                result = await asyncio.to_thread(
                    self.artifact_verifier.verify,
                    repository=self.github_actions_repository, run_id=payload["run_id"],
                    task_id=task_id, task_version=task.version,
                )
                return await self._respond(send, 200, {
                    "verified": True, "run_id": result.run_id, "task_id": result.task_id,
                    "task_version": result.task_version, "test_count": result.test_count,
                    "artifact_digest_sha256": result.artifact_digest_sha256,
                    "report_digest_sha256": result.report_digest_sha256,
                })
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
            evidence_match = _PROJECT_EVIDENCE.fullmatch(path)
            if evidence_match and method == "GET":
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id,
                    project_id=evidence_match.group(1), limit=500,
                )
                records = []
                if self.evidence is not None:
                    for task_id in task_ids:
                        task = await asyncio.to_thread(
                            self.tasks.get, actor_id=user.user_id, task_id=task_id,
                        )
                        verified_records = await asyncio.to_thread(
                            self.evidence.list_verified, owner_id=user.user_id,
                            task_id=task_id, task_version=task.version, limit=10,
                        )
                        records.extend({
                            "task_id": item.task_id,
                            "task_version": item.task_version,
                            "kind": item.kind,
                            "reference": item.reference,
                            "digest_sha256": item.digest_sha256,
                            "verifier_id": item.verifier_id,
                            "verified_at": item.verified_at,
                        } for item in verified_records)
                return await self._respond(send, 200, {"evidence": records})
            project_match = _PROJECT.fullmatch(path)
            if project_match and method == "GET":
                # Project lookup first prevents cross-owner project ID probing.
                task_ids = await asyncio.to_thread(
                    self.registry.list_task_ids, owner_id=user.user_id, project_id=project_match.group(1), limit=500,
                )
                evidence = []
                for task_id in task_ids:
                    task = await asyncio.to_thread(self.tasks.get, actor_id=user.user_id, task_id=task_id)
                    verified = (
                        await asyncio.to_thread(
                            self.evidence.list_verified, owner_id=user.user_id, task_id=task_id, task_version=task.version, limit=1,
                        ) if self.evidence is not None else ()
                    )
                    evidence.append(ProjectTaskEvidence(
                        task_id=task.task_id, owner_id=user.user_id, state=task.state,
                        updated_at=task.updated_at, dependencies=tuple(task.dependencies),
                        verification_ref=verified[0].reference if verified else None,
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
        except GitHubActionsDispatchError:
            return await self._respond(send, 409, {"error": "ci_dispatch_conflict"})
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
    def _awareness_dict(event):
        return {
            "event_id": event.event_id, "event_kind": event.event_kind,
            "summary": event.summary, "task_id": event.task_id,
            "task_version": event.task_version, "evidence_ref": event.evidence_ref,
            "selected_task_ids": event.selected_task_ids, "created_at": event.created_at,
        }

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
