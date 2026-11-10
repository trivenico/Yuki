"""Authenticated HTTP surface for paired-device inference jobs."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from core.device_pairing import DevicePairingError, DevicePairingService
from core.inference_jobs import InferenceJobService

_MAX_BODY = 270_000
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AuthenticatedInferenceAPI:
    """Route inference/device operations while delegating all other traffic.

    Browser sessions and device bearer credentials are deliberately separate.
    Owner IDs always come from the resolved browser session, never request data.
    """

    def __init__(self, app, auth_service, pairing: DevicePairingService,
                 jobs: InferenceJobService):
        if not isinstance(pairing, DevicePairingService):
            raise TypeError("pairing must be a DevicePairingService")
        if not isinstance(jobs, InferenceJobService):
            raise TypeError("jobs must be an InferenceJobService")
        self.app = app
        self.auth = auth_service
        self.pairing = pairing
        self.jobs = jobs

    def __getattr__(self, name):
        """Preserve the wrapped conversation API's existing runtime surface."""
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope.get("method", "GET").upper(), scope.get("path", "/")
        if not path.startswith("/v1/devices") and not path.startswith("/v1/inference"):
            await self.app(scope, receive, send)
            return
        try:
            if path == "/v1/devices/pair" and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"code", "name"} or not all(
                    isinstance(payload.get(k), str) for k in ("code", "name")
                ):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    credential = await asyncio.to_thread(
                        self.pairing.pair_device, payload["code"], payload["name"]
                    )
                except (DevicePairingError, ValueError):
                    return await self._respond(send, 401, {"error": "invalid_pairing_challenge"})
                return await self._respond(send, 201, {
                    "device_token": credential.token,
                    "device": self._device(credential.device),
                })

            worker_claim = path in {"/v1/inference/claim", "/v1/inference/worker/jobs/claim"}
            worker_completion = re.fullmatch(
                r"/v1/inference/(?:worker/jobs/|jobs/)?([0-9a-f]{32})/complete", path
            )
            if path.startswith("/v1/inference/worker/") or worker_claim or worker_completion:
                token = self._bearer(scope)
                if token is None:
                    return await self._respond(send, 401, {"error": "device_authentication_required"})
                if worker_claim and method == "POST":
                    payload = await self._body(receive)
                    if payload:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    try:
                        job = await asyncio.to_thread(self.jobs.claim_next, token)
                    except PermissionError:
                        return await self._respond(send, 401, {"error": "invalid_device_credential"})
                    if job is None:
                        return await self._respond(send, 200, {"job": None})
                    return await self._respond(send, 200, {"job": {
                        "job_id": job.job_id, "model_id": job.model_id,
                        "input_text": job.input_text,
                        "lease_expires_at": job.lease_expires_at,
                        "lease_token": job.lease_token,
                    }})
                match = worker_completion
                if match and method == "POST":
                    payload = await self._body(receive)
                    if set(payload) not in ({"lease_token", "result_text"}, {"lease_token", "error_code"}):
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    try:
                        job = await asyncio.to_thread(
                            self.jobs.complete_job, token, match.group(1),
                            lease_token=payload["lease_token"],
                            result_text=payload.get("result_text"),
                            error_code=payload.get("error_code"),
                        )
                    except PermissionError:
                        return await self._respond(send, 401, {"error": "invalid_device_credential"})
                    except LookupError:
                        return await self._respond(send, 404, {"error": "not_found"})
                    except ValueError:
                        return await self._respond(send, 409, {"error": "lease_rejected"})
                    return await self._respond(send, 200, {"job": job.public_dict()})
                return await self._respond(send, 404, {"error": "not_found"})

            user = await self._user(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})

            if path == "/v1/devices/pairing-challenges" and method == "POST":
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                challenge = await asyncio.to_thread(
                    self.pairing.create_pairing_challenge, user.user_id
                )
                return await self._respond(send, 201, {
                    "code": challenge.code, "expires_at": challenge.expires_at,
                })

            if path == "/v1/devices" and method == "GET":
                devices = await asyncio.to_thread(self.pairing.list_devices, user.user_id)
                return await self._respond(send, 200, {
                    "devices": [self._device(d) for d in devices],
                })

            match = re.fullmatch(r"/v1/devices/([^/]+)", path)
            if match and method == "DELETE":
                if not _DEVICE_ID.fullmatch(match.group(1)):
                    return await self._respond(send, 404, {"error": "not_found"})
                revoked = await asyncio.to_thread(
                    self.pairing.revoke_device, user.user_id, match.group(1)
                )
                return await self._respond(send, 200 if revoked else 404,
                                           {"revoked": True} if revoked else {"error": "not_found"})

            if path == "/v1/inference/jobs" and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"model_id", "device_id", "input_text"} or not all(
                    isinstance(payload.get(k), str) for k in ("model_id", "device_id", "input_text")
                ):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    job = await asyncio.to_thread(
                        self.jobs.create_job, user.user_id,
                        model_id=payload["model_id"], device_id=payload["device_id"],
                        input_text=payload["input_text"],
                    )
                except PermissionError:
                    return await self._respond(send, 404, {"error": "device_not_found"})
                return await self._respond(send, 201, {"job": job.public_dict()})

            if path == "/v1/inference/jobs" and method == "GET":
                jobs = await asyncio.to_thread(self.jobs.list_jobs, user.user_id)
                return await self._respond(send, 200, {
                    "jobs": [job.public_dict() for job in jobs],
                })

            match = re.fullmatch(r"/v1/inference/jobs/([0-9a-f]{32})(/cancel)?", path)
            if match and method == "GET" and not match.group(2):
                job = await asyncio.to_thread(self.jobs.get_job, user.user_id, match.group(1))
                return await self._respond(send, 200 if job else 404,
                                           {"job": job.public_dict()} if job else {"error": "not_found"})
            if match and method == "POST" and match.group(2) == "/cancel":
                cancelled = await asyncio.to_thread(
                    self.jobs.cancel_job, user.user_id, match.group(1)
                )
                return await self._respond(send, 200 if cancelled else 404,
                                           {"cancelled": True} if cancelled else {"error": "not_found"})
            return await self._respond(send, 404, {"error": "not_found"})
        except (ValueError, TypeError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 500, {"error": "internal_error"})

    async def _user(self, scope):
        token = self._bearer(scope)
        if token is None:
            return None
        return await asyncio.to_thread(self.auth.resolve_session, token)

    @staticmethod
    def _bearer(scope):
        headers = {k.lower(): v for k, v in scope.get("headers", [])
                   if isinstance(k, bytes) and isinstance(v, bytes)}
        raw = headers.get(b"authorization", b"").decode("latin-1")
        scheme, sep, token = raw.partition(" ")
        if not sep or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return token

    @staticmethod
    def _device(device):
        return {
            "device_id": device.device_id, "name": device.name,
            "created_at": device.created_at, "last_seen_at": device.last_seen_at,
        }

    @staticmethod
    async def _body(receive):
        chunks = bytearray()
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                raise ValueError("client disconnected")
            if event.get("type") != "http.request":
                continue
            chunks.extend(event.get("body", b""))
            if len(chunks) > _MAX_BODY:
                raise ValueError("request body too large")
            if not event.get("more_body", False):
                break
        value = json.loads(chunks.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
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
