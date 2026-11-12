"""Authenticated API for Yuki's owner-scoped data annotation workbench.

These routes manage local projects and human-reviewed annotations only. They
never submit annotations to a marketplace or client system.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import re

from core.annotation_assets import AnnotationAssetError, SQLiteAnnotationAssetStore
from core.annotation_image_preview import (
    AnnotationImagePreviewError,
    create_safe_png_preview,
)
from core.data_annotation import (
    AnnotationConflict,
    AnnotationPolicyError,
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

    def __init__(self, app, auth_service, workbench: SQLiteAnnotationWorkbench,
                 asset_store: SQLiteAnnotationAssetStore | None = None,
                 asset_scanner=None, reviewer_authorizer=None):
        if not isinstance(workbench, SQLiteAnnotationWorkbench):
            raise TypeError("workbench_must_be_SQLiteAnnotationWorkbench")
        self.app = app
        self.auth = auth_service
        self.workbench = workbench
        if asset_store is not None and not isinstance(asset_store, SQLiteAnnotationAssetStore):
            raise TypeError("asset_store_must_be_SQLiteAnnotationAssetStore")
        if asset_scanner is not None and not callable(asset_scanner):
            raise TypeError("asset_scanner_must_be_callable")
        self.asset_store = asset_store
        self.asset_scanner = asset_scanner
        if reviewer_authorizer is not None and not callable(reviewer_authorizer):
            raise TypeError("reviewer_authorizer_must_be_callable")
        self.reviewer_authorizer = reviewer_authorizer

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

            asset_upload = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/assets", path
            )
            if asset_upload and method == "POST":
                if self.asset_store is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                project_id = asset_upload.group(1)
                project = await asyncio.to_thread(
                    self.workbench.get_project, user.user_id, project_id
                )
                if project is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                headers = {key.lower(): value for key, value in scope.get("headers", [])
                           if isinstance(key, bytes) and isinstance(value, bytes)}
                content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
                if content_type != b"application/octet-stream":
                    return await self._respond(send, 415, {"error": "application_octet_stream_required"})
                data = await self._binary_body(receive, self.asset_store.max_bytes)
                asset = await asyncio.to_thread(
                    self.asset_store.upload, user.user_id, project_id, data
                )
                scan_status = "quarantined"
                if self.asset_scanner is not None:
                    try:
                        clean = await asyncio.to_thread(self.asset_scanner, asset.media_type, data)
                        if type(clean) is bool:
                            await asyncio.to_thread(
                                self.asset_store.mark_scan_result, user.user_id,
                                project_id, asset.asset_id, clean=clean
                            )
                            scan_status = "clean" if clean else "blocked"
                    except Exception:
                        pass
                return await self._respond(send, 201 if scan_status == "clean" else (422 if scan_status == "blocked" else 202), {
                    "asset_id": asset.asset_id, "media_type": asset.media_type,
                    "size_bytes": asset.size_bytes, "sha256": asset.sha256,
                    "scan_status": scan_status,
                })

            asset_access = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/assets/([0-9a-f]{32})",
                path,
            )
            asset_preview = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/assets/([0-9a-f]{32})/preview",
                path,
            )
            if asset_preview and method == "GET":
                if self.asset_store is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                project_id, asset_id = asset_preview.groups()
                try:
                    source = await asyncio.to_thread(
                        self.asset_store.read, user.user_id, project_id, asset_id
                    )
                except AnnotationAssetError:
                    return await self._respond(send, 409, {"error": "asset_integrity_check_failed"})
                try:
                    preview = await asyncio.to_thread(create_safe_png_preview, source)
                except RuntimeError:
                    return await self._respond(send, 503, {"error": "image_preview_unavailable"})
                except AnnotationImagePreviewError:
                    return await self._respond(send, 415, {"error": "unsupported_image_content"})
                return await self._respond_preview_png(send, preview)

            if asset_access:
                if self.asset_store is None:
                    return await self._respond(send, 404, {"error": "not_found"})
                project_id, asset_id = asset_access.groups()
                if method == "GET":
                    data, media_type = await asyncio.to_thread(
                        self._read_clean_asset, user.user_id, project_id, asset_id
                    )
                    return await self._respond_bytes(send, 200, data, media_type)
                if method == "DELETE":
                    deleted = await asyncio.to_thread(
                        self.asset_store.delete, user.user_id, project_id, asset_id
                    )
                    return await self._respond(send, 200 if deleted else 404, {"deleted": bool(deleted)})
                return await self._respond(send, 405, {"error": "method_not_allowed"})

            match = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/tasks/batch", path
            )
            if match and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"tasks"} or not isinstance(payload["tasks"], list):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                project_id = match.group(1)
                for task_data in payload["tasks"]:
                    if not isinstance(task_data, dict):
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    await self._validate_task_asset_ref(
                        user.user_id, project_id, task_data.get("asset_ref")
                    )
                tasks = await asyncio.to_thread(
                    self.workbench.create_tasks, user.user_id, project_id,
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

            draft_match = re.fullmatch(
                re.escape(prefix) + r"/tasks/([0-9a-f]{32})/draft", path
            )
            if draft_match:
                task_id = draft_match.group(1)
                if method == "GET":
                    draft = await asyncio.to_thread(
                        self.workbench.get_draft, user.user_id, task_id
                    )
                    return await self._respond(
                        send, 200, {"draft": asdict(draft) if draft else None}
                    )
                if method == "PUT":
                    payload = await self._body(receive)
                    if set(payload) != {"payload", "expected_revision"}:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    draft = await asyncio.to_thread(
                        self.workbench.save_draft, user.user_id, task_id, **payload
                    )
                    return await self._respond(
                        send, 200 if draft else 404,
                        {"draft": asdict(draft)} if draft else {"error": "not_found"},
                    )
                if method == "DELETE":
                    payload = await self._body(receive)
                    if set(payload) != {"expected_revision"}:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    deleted = await asyncio.to_thread(
                        self.workbench.delete_draft, user.user_id, task_id, **payload
                    )
                    return await self._respond(send, 200, {"deleted": deleted})
                return await self._respond(send, 405, {"error": "method_not_allowed"})

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
                    await self._validate_task_asset_ref(
                        user.user_id, project_id, payload.get("asset_ref")
                    )
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

            reviewer_membership = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/reviewers", path
            )
            if reviewer_membership:
                project_id = reviewer_membership.group(1)
                if method == "GET":
                    project = await asyncio.to_thread(
                        self.workbench.get_project, user.user_id, project_id
                    )
                    if project is None:
                        return await self._respond(send, 404, {"error": "not_found"})
                    reviewers = await asyncio.to_thread(
                        self.workbench.list_reviewers, user.user_id, project_id
                    )
                    return await self._respond(send, 200, {"reviewers": reviewers})
                if method in {"POST", "DELETE"}:
                    payload = await self._body(receive)
                    if set(payload) != {"reviewer_user_id"}:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    reviewer_id = payload["reviewer_user_id"]
                    if not isinstance(reviewer_id, str) or not reviewer_id:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    if method == "POST":
                        users = await asyncio.to_thread(self.auth.list_users)
                        if not any(item.user_id == reviewer_id for item in users):
                            return await self._respond(send, 404, {"error": "reviewer_not_found"})
                        granted = await asyncio.to_thread(
                            self.workbench.grant_reviewer, user.user_id, project_id, reviewer_id
                        )
                        return await self._respond(send, 201 if granted else 404, {
                            "granted": granted,
                        })
                    revoked = await asyncio.to_thread(
                        self.workbench.revoke_reviewer, user.user_id, project_id, reviewer_id
                    )
                    return await self._respond(send, 200, {"revoked": revoked})
                return await self._respond(send, 405, {"error": "method_not_allowed"})

            review_queue = re.fullmatch(
                re.escape(prefix) + r"/projects/([0-9a-f]{32})/review-queue", path
            )
            if review_queue and method == "GET":
                project_id = review_queue.group(1)
                owner_id = await self._reviewer_owner(user, project_id)
                if not isinstance(owner_id, str) or not owner_id or owner_id == user.user_id:
                    return await self._respond(send, 403, {"error": "reviewer_access_denied"})
                items = await asyncio.to_thread(
                    self.workbench.list_review_queue, owner_id, project_id,
                    reviewer_id=user.user_id if self.reviewer_authorizer is None else None,
                )
                if items is None:
                    return await self._respond(send, 403, {"error": "reviewer_access_denied"})
                return await self._respond(send, 200, {
                    "submissions": [asdict(item) for item in items],
                })

            match = re.fullmatch(
                re.escape(prefix) + r"/submissions/([0-9a-f]{32})/review", path
            )
            if match and method == "POST":
                payload = await self._body(receive)
                if set(payload) - {"decision", "notes", "project_id"} or not {"decision", "project_id"} <= set(payload):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                project_id = payload.pop("project_id")
                if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                owner_id = await self._reviewer_owner(user, project_id)
                if not isinstance(owner_id, str) or not owner_id or owner_id == user.user_id:
                    return await self._respond(send, 403, {"error": "reviewer_access_denied"})
                submission = await asyncio.to_thread(
                    self.workbench.review, owner_id, match.group(1),
                    reviewer_id=user.user_id, expected_project_id=project_id,
                    require_reviewer_grant=self.reviewer_authorizer is None,
                    **{key: value for key, value in payload.items() if key != "project_id"}
                )
                return await self._respond(
                    send, 200 if submission else 404,
                    {"submission": asdict(submission)} if submission else {"error": "not_found"},
                )
            return await self._respond(send, 404, {"error": "not_found"})
        except AnnotationConflict:
            return await self._respond(send, 409, {"error": "version_conflict"})
        except AnnotationPolicyError:
            return await self._respond(send, 403, {"error": "ai_assistance_not_permitted"})
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

    async def _reviewer_owner(self, user, project_id):
        if self.reviewer_authorizer is not None:
            return await asyncio.to_thread(self.reviewer_authorizer, user, project_id)
        return await asyncio.to_thread(
            self.workbench.reviewer_owner, user.user_id, project_id
        )

    async def _validate_task_asset_ref(self, owner_id, project_id, asset_ref):
        """Only bind a task to an existing, clean asset in this owner's project."""
        if asset_ref is None:
            return
        if self.asset_store is None:
            raise ValueError("annotation_asset_store_required")
        asset = await asyncio.to_thread(
            self.asset_store.get_asset, owner_id, project_id, asset_ref
        )
        if asset is None:
            raise ValueError("annotation_asset_not_clean_or_not_found")

    def _read_clean_asset(self, owner_id, project_id, asset_id):
        asset = self.asset_store.get_asset(owner_id, project_id, asset_id)
        if asset is None:
            raise LookupError("annotation_asset_not_found")
        return self.asset_store.read(owner_id, project_id, asset_id), asset.media_type

    @staticmethod
    async def _binary_body(receive, limit):
        chunks = bytearray()
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                raise ValueError("client_disconnected")
            if event.get("type") != "http.request":
                continue
            chunks.extend(event.get("body", b""))
            if len(chunks) > limit:
                raise ValueError("asset_too_large")
            if not event.get("more_body", False):
                break
        return bytes(chunks)

    @staticmethod
    async def _respond_preview_png(send, body):
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"image/png"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-disposition", b"inline"),
            (b"content-security-policy", b"default-src 'none'; sandbox"),
            (b"cross-origin-resource-policy", b"same-origin"),
        ]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _respond_bytes(send, status, body, media_type):
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", media_type.encode("ascii")),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-disposition", b"attachment"),
        ]})
        await send({"type": "http.response.body", "body": body})

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
