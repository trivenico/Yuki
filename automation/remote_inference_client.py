"""Authenticated remote inference client with firewall-pinned HTTPS egress."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from core.pinned_https_provider_transport import (
    PinnedHTTPSProviderTransport,
    PinnedHTTPTransportError,
)


@dataclass(frozen=True)
class RemoteInferenceJob:
    job_id: str
    model_id: str
    input_text: str
    lease_expires_at: str | None
    lease_token: str = field(repr=False, compare=False)


class RemoteInferenceJobClient:
    def __init__(self, base_url: str, device_token: str, *, lease_seconds: int = 120,
                 timeout: float = 15, max_response_bytes: int = 128_000,
                 firewall_policy=None, transport=None):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("invalid_api_url")
        parsed = urlsplit(base_url.strip().rstrip("/"))
        host = parsed.hostname
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)
                or not host or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("Yuki API must use HTTPS or loopback HTTP")
        if not isinstance(device_token, str) or not 20 <= len(device_token) <= 256:
            raise ValueError("invalid_device_credential")
        if type(lease_seconds) is not int or not 15 <= lease_seconds <= 3600:
            raise ValueError("invalid_lease_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
            raise ValueError("invalid_timeout")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 512_000:
            raise ValueError("invalid_response_limit")
        if transport is not None and not callable(getattr(transport, "open", None)):
            raise TypeError("transport must provide open(request, timeout=...)")
        if transport is not None and firewall_policy is not None:
            raise ValueError("provide a transport or firewall policy, not both")
        self.base_url = base_url.strip().rstrip("/")
        self.device_token = device_token
        self.lease_seconds = lease_seconds
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._transport = transport
        self._loopback_http = parsed.scheme == "http"
        if self._transport is None and not self._loopback_http:
            self._transport = PinnedHTTPSProviderTransport(
                allowed_hosts=(host,), timeout=timeout, firewall_policy=firewall_policy,
            )

    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method="POST", headers={
            "Authorization": "Bearer " + self.device_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        try:
            if self._transport is not None:
                response_context = self._transport.open(request, timeout=self.timeout)
            else:
                response_context = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}), NoRedirect(),
                ).open(request, timeout=self.timeout)
            with response_context as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RuntimeError("response_too_large")
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise RuntimeError("invalid_api_response")
                return body
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PermissionError("device_authentication_failed") from None
            if exc.code == 404:
                raise LookupError("inference_job_not_found") from None
            if exc.code in (400, 409):
                raise ValueError("inference_job_rejected") from None
            raise RuntimeError("Yuki inference API request failed") from None
        except PinnedHTTPTransportError:
            raise RuntimeError("Yuki inference API blocked or unavailable by network firewall") from None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError("Yuki inference API unavailable or returned invalid data") from None

    def claim_next(self, device_token=None):
        if device_token is not None and device_token != self.device_token:
            raise PermissionError("device_credential_mismatch")
        body = self._request("/v1/inference/claim")
        raw = body.get("job")
        if raw is None:
            return None
        if not isinstance(raw, dict) or set(raw) != {
            "job_id", "model_id", "input_text", "lease_expires_at", "lease_token"
        }:
            raise RuntimeError("invalid_claim_response")
        if not all(isinstance(raw[k], str) for k in ("job_id", "model_id", "input_text")):
            raise RuntimeError("invalid_claim_response")
        if not isinstance(raw["lease_token"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", raw["lease_token"]):
            raise RuntimeError("invalid_claim_response")
        if raw["lease_expires_at"] is not None and not isinstance(raw["lease_expires_at"], str):
            raise RuntimeError("invalid_claim_response")
        return RemoteInferenceJob(**raw)

    def complete_job(self, device_token, job_id, *, lease_token, result_text=None, error_code=None):
        if device_token != self.device_token:
            raise PermissionError("device_credential_mismatch")
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("invalid_job_id")
        if (result_text is None) == (error_code is None):
            raise ValueError("provide_exactly_one_result_or_error")
        if result_text is not None and (
            not isinstance(result_text, str) or len(result_text.encode("utf-8")) > 20_000
        ):
            raise ValueError("result_too_large")
        if error_code is not None and (not isinstance(error_code, str) or len(error_code) > 64):
            raise ValueError("invalid_error_code")
        if not isinstance(lease_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", lease_token):
            raise ValueError("invalid_lease_token")
        body = ({"lease_token": lease_token, "result_text": result_text}
                if result_text is not None else {"lease_token": lease_token, "error_code": error_code})
        response = self._request(f"/v1/inference/jobs/{job_id}/complete", body)
        if not isinstance(response.get("job"), dict):
            raise RuntimeError("invalid_completion_response")
        return response["job"]
