"""Authenticated HTTPS client for a paired device's incoming LAN call inbox.

This client handles invitation state only. It never opens audio/video devices,
starts WebRTC, or accepts an invitation without an explicit caller action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit


_SESSION_HANDLE = re.compile(r"^[0-9a-f]{64}$")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class RemoteCallInvitation:
    session_id: str = field(repr=False)
    caller_device_id: str
    recipient_device_id: str
    status: str
    created_at: str
    expires_at: str


class RemoteCallClient:
    """Read and explicitly accept/reject invitations for one paired device."""

    def __init__(self, base_url: str, device_token: str, *,
                 timeout: float = 10, max_response_bytes: int = 64_000):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("invalid_api_url")
        parsed = urlsplit(base_url.strip().rstrip("/"))
        host = parsed.hostname
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)
                or not host or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("Yuki API must use HTTPS or loopback HTTP")
        if not isinstance(device_token, str) or not 20 <= len(device_token) <= 256:
            raise ValueError("invalid_device_credential")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
            raise ValueError("invalid_timeout")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 256_000:
            raise ValueError("invalid_response_limit")
        self.base_url = base_url.strip().rstrip("/")
        self.device_token = device_token
        self.timeout = float(timeout)
        self.max_response_bytes = max_response_bytes

    def _request(self, method: str, path: str):
        request = urllib.request.Request(
            self.base_url + path,
            data=b"{}" if method == "POST" else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self.device_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=self.timeout
            ) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RuntimeError("response_too_large")
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise RuntimeError("invalid_api_response")
                return body
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PermissionError("device_authentication_failed_or_action_denied") from None
            if exc.code == 404:
                raise LookupError("call_invitation_not_found") from None
            if exc.code in (400, 409):
                raise ValueError("call_invitation_rejected") from None
            raise RuntimeError("Yuki call API request failed") from None
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError("Yuki call API unavailable or returned invalid data") from None

    @staticmethod
    def _invitation(raw) -> RemoteCallInvitation:
        expected = {
            "session_id", "caller_device_id", "recipient_device_id",
            "status", "created_at", "expires_at",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise RuntimeError("invalid_call_invitation")
        if not isinstance(raw["session_id"], str) or not _SESSION_HANDLE.fullmatch(raw["session_id"]):
            raise RuntimeError("invalid_call_invitation")
        if not all(isinstance(raw[key], str) and _DEVICE_ID.fullmatch(raw[key])
                   for key in ("caller_device_id", "recipient_device_id")):
            raise RuntimeError("invalid_call_invitation")
        if raw["status"] != "ringing":
            raise RuntimeError("invalid_call_invitation")
        if not all(isinstance(raw[key], str) and 0 < len(raw[key]) <= 64
                   for key in ("created_at", "expires_at")):
            raise RuntimeError("invalid_call_invitation")
        return RemoteCallInvitation(**raw)

    def list_incoming(self) -> list[RemoteCallInvitation]:
        body = self._request("GET", "/v1/devices/calls/incoming")
        rows = body.get("invitations")
        if not isinstance(rows, list) or len(rows) > 10:
            raise RuntimeError("invalid_call_inbox")
        invitations = [self._invitation(row) for row in rows]
        if len({item.session_id for item in invitations}) != len(invitations):
            raise RuntimeError("duplicate_call_invitation")
        return invitations

    def _respond(self, session_id: str, action: str) -> dict:
        if not isinstance(session_id, str) or not _SESSION_HANDLE.fullmatch(session_id):
            raise ValueError("invalid_call_invitation_handle")
        body = self._request("POST", f"/v1/devices/calls/{session_id}/{action}")
        session = body.get("session")
        if not isinstance(session, dict) or session.get("status") != (
            "accepted" if action == "accept" else "rejected"
        ):
            raise RuntimeError("invalid_call_action_response")
        if session.get("session_id") != session_id:
            raise RuntimeError("call_action_session_mismatch")
        return session

    def accept(self, session_id: str) -> dict:
        """Accept one invitation after the local user explicitly chooses Accept."""
        return self._respond(session_id, "accept")

    def reject(self, session_id: str) -> dict:
        """Reject one invitation after the local user explicitly chooses Decline."""
        return self._respond(session_id, "reject")
