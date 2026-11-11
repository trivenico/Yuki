"""Authenticated HTTP surface for paired-device inference jobs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from core.device_pairing import DevicePairingError, DevicePairingService
from core.inference_jobs import InferenceJobService
from core.call_confirmation import (CallConfirmationDenied, CallConfirmationError, SQLiteCallConfirmationStore)
from core.lan_call_sessions import LANCallSessionStore, CallSessionDenied, CallSessionError

_MAX_BODY = 70_000
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
        self.calls = LANCallSessionStore(pairing)
        self.call_confirmations = SQLiteCallConfirmationStore(pairing.database_path)

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
                    if ("result_text" in payload and
                            (not isinstance(payload["result_text"], str) or
                             len(payload["result_text"].encode("utf-8")) > 20_000)):
                        return await self._respond(send, 413, {"error": "result_too_large"})
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


            call_incoming = path == "/v1/devices/calls/incoming" and method == "GET"
            call_action = re.fullmatch(r"/v1/devices/calls/([A-Za-z0-9_-]{32,128})/(accept|reject|end)", path)
            call_read = re.fullmatch(r"/v1/devices/calls/([A-Za-z0-9_-]{32,128})", path) if method == "GET" else None
            if call_incoming or (call_action and method == "POST") or call_read:
                device_token = self._bearer(scope)
                if device_token is None:
                    return await self._respond(send, 401, {"error": "device_authentication_required"})
                try:
                    if call_incoming:
                        invitations = await asyncio.to_thread(self.calls.list_incoming, device_token=device_token)
                        return await self._respond(send, 200, {"invitations": invitations})
                    if call_action:
                        session_id, action = call_action.group(1), call_action.group(2)
                        if action == "accept":
                            session = await asyncio.to_thread(self.calls.accept, session_id=session_id, recipient_token=device_token)
                        elif action == "reject":
                            session = await asyncio.to_thread(self.calls.reject, session_id=session_id, recipient_token=device_token)
                        else:
                            session = await asyncio.to_thread(self.calls.end, session_id=session_id, device_token=device_token)
                    else:
                        session = await asyncio.to_thread(self.calls.get, session_id=call_read.group(1), device_token=device_token)
                    return await self._respond(send, 200, {"session": self._call(session)})
                except CallSessionDenied:
                    return await self._respond(send, 403, {"error": "call_action_denied"})
                except CallSessionError:
                    return await self._respond(send, 400, {"error": "invalid_call_request"})

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


            if path == "/v1/devices/calls/confirmation-challenges" and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"password", "caller_device_id", "recipient_device_id"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if not all(isinstance(payload.get(k), str) for k in ("password", "caller_device_id", "recipient_device_id")):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if not _DEVICE_ID.fullmatch(payload["caller_device_id"]) or not _DEVICE_ID.fullmatch(payload["recipient_device_id"]):
                    return await self._respond(send, 400, {"error": "invalid_call_request"})
                if payload["caller_device_id"] == payload["recipient_device_id"]:
                    return await self._respond(send, 400, {"error": "invalid_call_request"})
                session_token = self._bearer(scope)
                try:
                    await asyncio.to_thread(self.auth.verify_session_password, session_token, payload["password"])
                    owns = await asyncio.to_thread(
                        lambda: self.pairing.owns_active_device(user.user_id, payload["caller_device_id"])
                        and self.pairing.owns_active_device(user.user_id, payload["recipient_device_id"])
                    )
                    if not owns:
                        return await self._respond(send, 404, {"error": "device_not_found"})
                    token, expires_at = await asyncio.to_thread(
                        self.call_confirmations.issue,
                        owner_id=user.user_id,
                        authenticated_session_token=session_token,
                        caller_device_id=payload["caller_device_id"],
                        recipient_device_id=payload["recipient_device_id"],
                    )
                except PermissionError:
                    return await self._respond(send, 403, {"error": "reauthentication_required"})
                except (CallConfirmationDenied, CallConfirmationError):
                    return await self._respond(send, 403, {"error": "confirmation_denied"})
                return await self._respond(send, 201, {"confirmation_token": token, "expires_at": expires_at})

            owner_cancel = re.fullmatch(
                r"/v1/devices/calls/([0-9a-f]{64})/cancel", path
            )
            if owner_cancel and method == "POST":
                user = await self._user(scope)
                if user is None:
                    return await self._respond(send, 401, {"error": "authentication_required"})
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    session = await asyncio.to_thread(
                        self.calls.cancel_as_owner,
                        owner_id=user.user_id,
                        session_id=owner_cancel.group(1),
                    )
                except CallSessionDenied:
                    return await self._respond(send, 404, {"error": "call_not_cancellable"})
                except CallSessionError:
                    return await self._respond(send, 400, {"error": "invalid_call_request"})
                return await self._respond(send, 200, {"session": self._call(session)})

            if path == "/v1/devices/calls" and method == "GET":
                user = await self._user(scope)
                if user is None:
                    return await self._respond(send, 401, {"error": "authentication_required"})
                try:
                    sessions = await asyncio.to_thread(
                        self.calls.list_for_owner, owner_id=user.user_id, limit=50
                    )
                except CallSessionError:
                    return await self._respond(send, 400, {"error": "invalid_call_request"})
                return await self._respond(send, 200, {
                    "sessions": [self._call(session) for session in sessions],
                })

            if path == "/v1/devices/calls" and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"caller_device_id", "recipient_device_id", "confirmation_token"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if not all(isinstance(payload.get(k), str) for k in ("caller_device_id", "recipient_device_id", "confirmation_token")):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                session_token = self._bearer(scope)
                try:
                    await asyncio.to_thread(
                        self.call_confirmations.consume,
                        token=payload["confirmation_token"],
                        owner_id=user.user_id,
                        authenticated_session_token=session_token,
                        caller_device_id=payload["caller_device_id"],
                        recipient_device_id=payload["recipient_device_id"],
                    )
                    session = await asyncio.to_thread(
                        self.calls.invite_as_owner, owner_id=user.user_id,
                        caller_device_id=payload["caller_device_id"],
                        recipient_device_id=payload["recipient_device_id"],
                    )
                except CallConfirmationDenied:
                    return await self._respond(send, 403, {"error": "confirmation_required_or_expired"})
                except CallSessionDenied:
                    return await self._respond(send, 403, {"error": "call_invitation_denied"})
                except (CallSessionError, CallConfirmationError):
                    return await self._respond(send, 400, {"error": "invalid_call_request"})
                return await self._respond(send, 201, {"session": self._call(session)})

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
                    "jobs": [self._job_summary(job) for job in jobs],
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
    def _call(session):
        # Never return the original random session secret. A SHA-256 digest is
        # the opaque handle used consistently by recipient inbox and owner API.
        raw_id = session.session_id
        handle = (raw_id if isinstance(raw_id, str) and len(raw_id) == 64
                  and all(ch in "0123456789abcdef" for ch in raw_id)
                  else hashlib.sha256(raw_id.encode("ascii")).hexdigest())
        return {
            "session_id": handle,
            "caller_device_id": session.caller_device_id,
            "recipient_device_id": session.recipient_device_id,
            "status": session.status,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "accepted_at": session.accepted_at,
            "ended_at": session.ended_at,
        }

    @staticmethod
    def _job_summary(job):
        """Keep collection responses small; fetch an individual job for its result."""
        value = job.public_dict()
        value.pop("result_text", None)
        return value

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
