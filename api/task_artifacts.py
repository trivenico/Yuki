"""Authenticated HTTP retrieval for task-scoped immutable artifacts."""
from __future__ import annotations

import asyncio
import re

from automation.task_runtime import TaskRuntime

_PATH = re.compile(r"^/v1/tasks/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/artifacts/([0-9a-f]{64})$")


class AuthenticatedTaskArtifactAPI:
    """Serve task artifacts only to their authenticated, authorized owner."""

    def __init__(self, app, auth_service, task_runtime: TaskRuntime):
        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("task_runtime must be a TaskRuntime")
        self.app = app
        self.auth = auth_service
        self.tasks = task_runtime

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        match = _PATH.fullmatch(path)
        if match is None:
            if path.startswith("/v1/tasks/") and "/artifacts/" in path:
                return await self._respond(send, 404, b'{"error":"not_found"}', method)
            await self.app(scope, receive, send)
            return
        if method not in {"GET", "HEAD"}:
            return await self._respond(send, 405, b'{"error":"method_not_allowed"}', method)
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return await self._respond(send, 401, b'{"error":"authentication_required"}', method)
        user = await asyncio.to_thread(self.auth.resolve_session, token)
        if user is None:
            return await self._respond(send, 401, b'{"error":"authentication_required"}', method)
        task_id, digest = match.groups()
        reference = f"yuki-artifact://sha256/{digest}"
        try:
            content = await asyncio.to_thread(
                self.tasks.get_artifact,
                actor_id=user.user_id,
                task_id=task_id,
                reference=reference,
            )
        except PermissionError:
            return await self._respond(send, 403, b'{"error":"artifact_access_denied"}', method)
        except KeyError:
            return await self._respond(send, 404, b'{"error":"not_found"}', method)
        except ValueError:
            return await self._respond(send, 400, b'{"error":"invalid_artifact_reference"}', method)
        except Exception:
            return await self._respond(send, 503, b'{"error":"artifact_storage_unavailable"}', method)
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/octet-stream"),
                (b"content-length", str(len(content)).encode("ascii")),
                (b"content-disposition", f'attachment; filename="artifact-{digest}"'.encode("ascii")),
                (b"cache-control", b"private, no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b"" if method == "HEAD" else content,
        })

    @staticmethod
    async def _respond(send, status, body, method="GET"):
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
        await send({
            "type": "http.response.body",
            "body": b"" if method == "HEAD" else body,
        })
