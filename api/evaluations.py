"""Authenticated API for trusted, registered model evaluations."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import re
from urllib.parse import parse_qs

from evaluation.run_store import EvaluationRunConflict, EvaluationRunInProgress
from evaluation.service import EvaluationCapacityError, EvaluationService

_MAX_BODY = 4_096
_MAX_QUERY = 1_024
_PREFIX = "/v1/evaluations/runs"
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{16,100}$")
_ALLOWED_CREATE = frozenset({
    "benchmark_id", "benchmark_version", "benchmark_digest", "candidate_id",
})


class AuthenticatedEvaluationAPI:
    """Owner-session routes; caller data cannot supply reports or provider IDs."""

    def __init__(self, app, auth_service, evaluation_service: EvaluationService):
        if not isinstance(evaluation_service, EvaluationService):
            raise TypeError("evaluation_service must be an EvaluationService")
        self.app = app
        self.auth = auth_service
        self.evaluations = evaluation_service

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        if path != _PREFIX and not path.startswith(_PREFIX + "/"):
            await self.app(scope, receive, send)
            return
        try:
            user = await self._user(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            if path == _PREFIX and method == "POST":
                payload = await self._body(receive)
                if set(payload) != _ALLOWED_CREATE:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if (
                    type(payload["benchmark_version"]) is not int
                    or not isinstance(payload["benchmark_id"], str)
                    or not isinstance(payload["benchmark_digest"], str)
                    or not isinstance(payload["candidate_id"], str)
                ):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                headers = self._headers(scope)
                key = headers.get(b"idempotency-key", b"").decode("latin-1")
                if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", key):
                    return await self._respond(send, 400, {"error": "idempotency_key_required"})
                run = await asyncio.to_thread(
                    self.evaluations.run,
                    owner_id=user.user_id,
                    idempotency_key=key,
                    benchmark_id=payload["benchmark_id"],
                    benchmark_version=payload["benchmark_version"],
                    benchmark_digest=payload["benchmark_digest"],
                    candidate_id=payload["candidate_id"],
                )
                return await self._respond(send, 201, {"run": self._public_run(run)})
            if path == _PREFIX and method == "GET":
                raw_query = scope.get("query_string", b"")
                if not isinstance(raw_query, bytes) or len(raw_query) > _MAX_QUERY:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                query = parse_qs(raw_query.decode("ascii"), keep_blank_values=True, strict_parsing=True)
                if set(query) - {"limit"} or any(len(values) != 1 for values in query.values()):
                    return await self._respond(send, 400, {"error": "invalid_query"})
                limit = 100
                if "limit" in query:
                    raw_limit = query["limit"][0]
                    if not re.fullmatch(r"[0-9]{1,4}", raw_limit):
                        return await self._respond(send, 400, {"error": "invalid_query"})
                    limit = int(raw_limit)
                runs = await asyncio.to_thread(
                    self.evaluations.store.list_runs, owner_id=user.user_id, limit=limit,
                )
                return await self._respond(send, 200, {"runs": [self._public_run(run) for run in runs]})
            match = re.fullmatch(re.escape(_PREFIX) + r"/([A-Za-z0-9_-]{16,100})", path)
            if match and method == "GET":
                run = await asyncio.to_thread(
                    self.evaluations.store.get, owner_id=user.user_id, run_id=match.group(1),
                )
                return await self._respond(send, 200, {"run": self._public_run(run)})
            if path == _PREFIX or path.startswith(_PREFIX + "/"):
                if path != _PREFIX and not re.fullmatch(
                    re.escape(_PREFIX) + r"/[A-Za-z0-9_-]{16,100}", path
                ):
                    return await self._respond(send, 404, {"error": "not_found"})
                return await self._respond(send, 405, {"error": "method_not_allowed"})
        except EvaluationRunInProgress:
            return await self._respond(send, 409, {"error": "evaluation_in_progress"})
        except EvaluationCapacityError:
            return await self._respond(send, 429, {"error": "evaluation_capacity_reached"})
        except EvaluationRunConflict:
            return await self._respond(send, 409, {"error": "idempotency_conflict"})
        except KeyError:
            return await self._respond(send, 404, {"error": "not_found"})
        except (ValueError, TypeError, UnicodeDecodeError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 500, {"error": "internal_error"})

    @staticmethod
    def _public_run(run):
        value = asdict(run)
        for field in ("owner_id", "idempotency_key", "request_digest", "record_digest"):
            value.pop(field, None)
        return value

    @staticmethod
    def _headers(scope):
        return {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }

    async def _user(self, scope):
        raw = self._headers(scope).get(b"authorization", b"").decode("latin-1")
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
        value = json.loads(chunks.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("request_must_be_object")
        return value

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
