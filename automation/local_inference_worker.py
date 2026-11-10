"""Explicit, bounded local Ollama worker for authenticated inference jobs.

No network listener or background thread is created. The embedding application
must call run_once explicitly. Model IDs are resolved through an operator-
configured allowlist; jobs cannot choose arbitrary endpoints or tools.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Any, TYPE_CHECKING
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
    def __init__(self, jobs: InferenceJobService, device_token: str,
                 providers: Mapping[str, OllamaProvider], *, max_output_bytes: int = 200_000):
        if not callable(getattr(jobs, "claim_next", None)) or not callable(getattr(jobs, "complete_job", None)):
            raise TypeError("jobs must provide claim_next and complete_job")
        if type(getattr(jobs, "lease_seconds", None)) is not int:
            raise TypeError("jobs must expose an integer lease_seconds")
        if not isinstance(device_token, str) or not 20 <= len(device_token) <= 256:
            raise ValueError("invalid_device_credential")
        if not isinstance(providers, Mapping) or not providers or len(providers) > 32:
            raise ValueError("configure between 1 and 32 model providers")
        normalized = {}
        for model_id, provider in providers.items():
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 120:
                raise ValueError("invalid_model_allowlist_entry")
            if not isinstance(provider, OllamaProvider):
                raise TypeError("each provider must be an OllamaProvider")
            # A synchronous request must fit inside its device job lease.
            # Keep a safety margin for scheduling and result persistence.
            if jobs.lease_seconds <= provider.timeout + 15:
                raise ValueError("job lease must exceed Ollama timeout by at least 15 seconds")
            normalized[model_id] = provider
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 256_000:
            raise ValueError("invalid_max_output_bytes")
        self.jobs, self.device_token = jobs, device_token
        self.providers, self.max_output_bytes = normalized, max_output_bytes

    def run_once(self) -> WorkerRun:
        job = self.jobs.claim_next(self.device_token)
        if job is None:
            return WorkerRun(False, None, "idle")
        provider = self.providers.get(job.model_id)
        if provider is None:
            self.jobs.complete_job(self.device_token, job.job_id, lease_token=job.lease_token, error_code="model_not_allowlisted")
            return WorkerRun(True, job.job_id, "failed", "model_not_allowlisted")
        try:
            if not provider.is_available():
                self.jobs.complete_job(self.device_token, job.job_id, error_code="model_unavailable")
                return WorkerRun(True, job.job_id, "failed", "model_unavailable")
            response = provider.generate_chat(
                messages=[{"role": "user", "content": job.input_text}],
                tools=[], tool_choice="none",
            )
            if not isinstance(response, dict) or not isinstance(response.get("text"), str):
                raise ValueError("invalid_provider_response")
            output = response["text"]
            if len(output.encode("utf-8")) > self.max_output_bytes:
                self.jobs.complete_job(self.device_token, job.job_id, error_code="output_too_large")
                return WorkerRun(True, job.job_id, "failed", "output_too_large")
            self.jobs.complete_job(self.device_token, job.job_id, lease_token=job.lease_token, result_text=output)
            return WorkerRun(True, job.job_id, "succeeded")
        except Exception:
            # Do not expose endpoint details, credentials, prompts or exception text
            # through persisted job state. The host may log a sanitized traceback.
            try:
                self.jobs.complete_job(self.device_token, job.job_id, error_code="inference_failed")
            except (LookupError, ValueError, PermissionError):
                pass
            return WorkerRun(True, job.job_id, "failed", "inference_failed")
