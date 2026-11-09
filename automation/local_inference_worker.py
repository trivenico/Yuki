"""Explicit local Ollama worker with fenced, renewable job claims.

No listener or persistent background thread is created. The embedding
application calls run_once explicitly; a temporary lease-renewal thread exists
only while one synchronous provider request is in progress.
"""
from __future__ import annotations
from dataclasses import dataclass
from threading import Event, Thread
from typing import Mapping, Any
from providers.ollama import OllamaProvider

@dataclass(frozen=True)
class WorkerRun:
    claimed: bool
    job_id: str | None
    status: str
    error_code: str | None = None

class _LeaseLost(Exception):
    pass

class LocalInferenceWorker:
    def __init__(self, jobs: Any, device_token: str,
                 providers: Mapping[str, OllamaProvider], *, max_output_bytes: int = 200_000):
        if (not callable(getattr(jobs, "claim_next", None))
                or not callable(getattr(jobs, "complete_job", None))
                or not callable(getattr(jobs, "renew_job_lease", None))):
            raise TypeError("jobs must provide claim_next, complete_job and renew_job_lease")
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
            if jobs.lease_seconds <= provider.timeout + 2:
                raise ValueError("job lease must exceed Ollama timeout by at least 2 seconds")
            normalized[model_id] = provider
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 256_000:
            raise ValueError("invalid_max_output_bytes")
        self.jobs, self.device_token = jobs, device_token
        self.providers, self.max_output_bytes = normalized, max_output_bytes
        self._renew_interval = max(1.0, min(30.0, jobs.lease_seconds / 3.0))

    def _claim_credentials(self, job):
        generation = getattr(job, "claim_generation", None)
        token = getattr(job, "claim_token", None)
        if type(generation) is not int or generation < 1:
            raise PermissionError("missing_claim_generation")
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise PermissionError("missing_claim_credential")
        return generation, token

    def _complete(self, job, *, result_text=None, error_code=None):
        """Complete only the exact claim returned to this worker invocation."""
        generation, token = self._claim_credentials(job)
        if (result_text is None) == (error_code is None):
            raise ValueError("provide_exactly_one_result_or_error")
        return self.jobs.complete_job(
            self.device_token, job.job_id,
            claim_generation=generation, claim_token=token,
            result_text=result_text, error_code=error_code,
        )

    def _generate_with_lease(self, job, provider):
        """Renew in parallel with synchronous inference and fail closed on loss."""
        generation, token = self._claim_credentials(job)
        stop = Event()
        lost = Event()

        def renew():
            while not stop.wait(self._renew_interval):
                try:
                    self.jobs.renew_job_lease(
                        self.device_token, job.job_id,
                        claim_generation=generation, claim_token=token,
                    )
                except Exception:
                    # Any inability to prove ownership means no result may commit.
                    lost.set()
                    return

        renewal_thread = Thread(target=renew, name="yuki-job-lease-renewal", daemon=True)
        renewal_thread.start()
        response = None
        provider_error = None
        try:
            response = provider.generate_chat(
                messages=[{"role": "user", "content": job.input_text}],
                tools=[], tool_choice="none",
            )
        except Exception as exc:
            provider_error = exc
        finally:
            stop.set()
            renewal_thread.join(timeout=6.0)
            if renewal_thread.is_alive():
                lost.set()

        if lost.is_set():
            raise _LeaseLost()
        if provider_error is not None:
            raise provider_error
        return response

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
            response = self._generate_with_lease(job, provider)
            if not isinstance(response, dict) or not isinstance(response.get("text"), str):
                raise ValueError("invalid_provider_response")
            output = response["text"]
            if len(output.encode("utf-8")) > self.max_output_bytes:
                self._complete(job, error_code="output_too_large")
                return WorkerRun(True, job.job_id, "failed", "output_too_large")
            self._complete(job, result_text=output)
            return WorkerRun(True, job.job_id, "succeeded")
        except _LeaseLost:
            return WorkerRun(True, job.job_id, "failed", "lease_lost")
        except Exception:
            # Persist only a sanitized code, never endpoint details or prompts.
            try:
                self._complete(job, error_code="inference_failed")
            except (LookupError, ValueError, PermissionError):
                pass
            return WorkerRun(True, job.job_id, "failed", "inference_failed")
