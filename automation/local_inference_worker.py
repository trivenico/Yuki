"""Bounded local Ollama worker for authenticated, lease-fenced inference jobs.

The worker is deliberately pull-based: callers invoke run_once from an
application-managed process. It creates no threads, subprocesses or listeners.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TYPE_CHECKING

from providers.ollama import OllamaProvider

if TYPE_CHECKING:
    from core.inference_jobs import InferenceJobService


@dataclass(frozen=True)
class WorkerRun:
    claimed: bool
    job_id: str | None
    status: str
    error_code: str | None = None


class LocalInferenceWorker:
    def __init__(
        self,
        jobs: InferenceJobService,
        device_token: str,
        providers: Mapping[str, OllamaProvider],
        *,
        max_output_bytes: int = 200_000,
    ):
        if not callable(getattr(jobs, "claim_next", None)) or not callable(
            getattr(jobs, "complete_job", None)
        ):
            raise TypeError("jobs_must_provide_claim_and_completion")
        if type(getattr(jobs, "lease_seconds", None)) is not int:
            raise TypeError("jobs_must_expose_integer_lease_seconds")
        if not isinstance(device_token, str) or not 20 <= len(device_token) <= 256:
            raise ValueError("invalid_device_credential")
        if not isinstance(providers, Mapping) or not 1 <= len(providers) <= 32:
            raise ValueError("configure_between_1_and_32_model_providers")
        normalized: dict[str, OllamaProvider] = {}
        for model_id, provider in providers.items():
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 120:
                raise ValueError("invalid_model_allowlist_entry")
            if not isinstance(provider, OllamaProvider):
                raise TypeError("each_provider_must_be_an_OllamaProvider")
            if jobs.lease_seconds <= provider.timeout + 15:
                raise ValueError("job_lease_must_exceed_provider_timeout_by_15_seconds")
            normalized[model_id] = provider
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 200_000:
            raise ValueError("invalid_max_output_bytes")
        self.jobs = jobs
        self.device_token = device_token
        self.providers = normalized
        self.max_output_bytes = max_output_bytes

    def _complete(self, job, *, result_text=None, error_code=None):
        lease_token = getattr(job, "lease_token", None)
        if not isinstance(lease_token, str) or not 40 <= len(lease_token) <= 64:
            raise PermissionError("missing_current_job_lease")
        return self.jobs.complete_job(
            self.device_token,
            job.job_id,
            lease_token=lease_token,
            result_text=result_text,
            error_code=error_code,
        )

    def run_once(self) -> WorkerRun:
        job = self.jobs.claim_next(self.device_token)
        if job is None:
            return WorkerRun(False, None, "idle")
        provider = self.providers.get(job.model_id)
        if provider is None:
            self._complete(job, error_code="model_not_allowlisted")
            return WorkerRun(True, job.job_id, "failed", "model_not_allowlisted")
        try:
            if not provider.is_available():
                self._complete(job, error_code="model_unavailable")
                return WorkerRun(True, job.job_id, "failed", "model_unavailable")
            response = provider.generate_chat(
                messages=[{"role": "user", "content": job.input_text}],
                tools=[],
                tool_choice="none",
            )
            if not isinstance(response, dict) or not isinstance(response.get("text"), str):
                raise ValueError("invalid_provider_response")
            output = response["text"]
            if len(output.encode("utf-8")) > self.max_output_bytes:
                self._complete(job, error_code="output_too_large")
                return WorkerRun(True, job.job_id, "failed", "output_too_large")
            self._complete(job, result_text=output)
            return WorkerRun(True, job.job_id, "succeeded")
        except PermissionError:
            # Credential revocation is terminal for this worker process.
            raise
        except Exception:
            # Persist a stable code, never provider exception details or prompts.
            try:
                self._complete(job, error_code="inference_failed")
            except (LookupError, ValueError, PermissionError):
                # A reclaimed, cancelled, expired or revoked claim is no longer ours.
                pass
            return WorkerRun(True, job.job_id, "failed", "inference_failed")
