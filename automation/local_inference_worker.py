"""Bounded local Ollama worker with claim heartbeats and hard result deadlines.

A provider call that cannot be interrupted may finish in its single bounded
daemon thread after the deadline. Its result is discarded; another call is
not started until that thread exits.
"""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time
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
        execution_timeout: float = 300,
        heartbeat_interval: float | None = None,
        monotonic=None,
    ):
        if not callable(getattr(jobs, "claim_next", None)) or not callable(
            getattr(jobs, "complete_job", None)
        ) or not callable(getattr(jobs, "renew_job", None)):
            raise TypeError("jobs_must_provide_claim_completion_and_renewal")
        if type(getattr(jobs, "lease_seconds", None)) is not int:
            raise TypeError("jobs_must_expose_integer_lease_seconds")
        if not isinstance(device_token, str) or not 20 <= len(device_token) <= 256:
            raise ValueError("invalid_device_credential")
        if not isinstance(providers, Mapping) or not 1 <= len(providers) <= 32:
            raise ValueError("configure_between_1_and_32_model_providers")
        if isinstance(execution_timeout, bool) or not isinstance(execution_timeout, (int, float)) or not 1 <= execution_timeout <= 3600:
            raise ValueError("invalid_execution_timeout")
        if heartbeat_interval is None:
            heartbeat_interval = min(10.0, jobs.lease_seconds / 3)
        if (isinstance(heartbeat_interval, bool) or not isinstance(heartbeat_interval, (int, float))
                or not 0.1 <= heartbeat_interval < jobs.lease_seconds):
            raise ValueError("invalid_heartbeat_interval")
        normalized: dict[str, OllamaProvider] = {}
        for model_id, provider in providers.items():
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 120:
                raise ValueError("invalid_model_allowlist_entry")
            if not isinstance(provider, OllamaProvider):
                raise TypeError("each_provider_must_be_an_OllamaProvider")
            normalized[model_id] = provider
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 200_000:
            raise ValueError("invalid_max_output_bytes")
        if monotonic is not None and not callable(monotonic):
            raise TypeError("monotonic_must_be_callable")
        self.jobs = jobs
        self.device_token = device_token
        self.providers = normalized
        self.max_output_bytes = max_output_bytes
        self.execution_timeout = float(execution_timeout)
        self.heartbeat_interval = float(heartbeat_interval)
        self._monotonic = monotonic or time.monotonic
        self._provider_thread = None
        self._provider_lock = threading.Lock()

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

    def _run_provider(self, provider, job, result_queue):
        try:
            if not provider.is_available():
                result_queue.put((False, "model_unavailable"))
                return
            response = provider.generate_chat(
                messages=[{"role": "user", "content": job.input_text}],
                tools=[],
                tool_choice="none",
            )
            if not isinstance(response, dict) or not isinstance(response.get("text"), str):
                raise ValueError("invalid_provider_response")
            output = response["text"]
            if len(output.encode("utf-8")) > self.max_output_bytes:
                result_queue.put((False, "output_too_large"))
                return
            result_queue.put((True, output))
        except Exception:
            # Never return provider exception details, prompts or endpoint data.
            result_queue.put((False, "inference_failed"))

    def run_once(self) -> WorkerRun:
        with self._provider_lock:
            if self._provider_thread is not None and self._provider_thread.is_alive():
                return WorkerRun(False, None, "busy", "previous_provider_call_still_running")
            self._provider_thread = None

        job = self.jobs.claim_next(self.device_token)
        if job is None:
            return WorkerRun(False, None, "idle")
        provider = self.providers.get(job.model_id)
        if provider is None:
            self._complete(job, error_code="model_not_allowlisted")
            return WorkerRun(True, job.job_id, "failed", "model_not_allowlisted")

        result_queue = queue.Queue(maxsize=1)
        provider_thread = threading.Thread(
            target=self._run_provider, args=(provider, job, result_queue),
            name="yuki-local-inference", daemon=True,
        )
        with self._provider_lock:
            self._provider_thread = provider_thread
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()
        deadline = self._monotonic() + self.execution_timeout

        def heartbeat():
            while not stop_heartbeat.wait(self.heartbeat_interval):
                if self._monotonic() >= deadline:
                    return
                try:
                    self.jobs.renew_job(
                        self.device_token, job.job_id, lease_token=job.lease_token,
                    )
                except Exception:
                    lease_lost.set()
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat, name="yuki-inference-lease-heartbeat", daemon=True,
        )
        provider_thread.start()
        heartbeat_thread.start()
        outcome = None
        deadline_exceeded = False
        try:
            while True:
                if lease_lost.is_set():
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    deadline_exceeded = True
                    break
                try:
                    outcome = result_queue.get(timeout=min(0.1, remaining))
                    break
                except queue.Empty:
                    continue
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()

        if lease_lost.is_set():
            return WorkerRun(True, job.job_id, "unknown", "lease_renewal_failed")
        if deadline_exceeded:
            try:
                self._complete(job, error_code="execution_deadline_exceeded")
            except (LookupError, ValueError, PermissionError):
                pass
            return WorkerRun(True, job.job_id, "failed", "execution_deadline_exceeded")
        succeeded, value = outcome
        try:
            if succeeded:
                self._complete(job, result_text=value)
                return WorkerRun(True, job.job_id, "succeeded")
            self._complete(job, error_code=value)
            return WorkerRun(True, job.job_id, "failed", value)
        except PermissionError:
            raise
        except (LookupError, ValueError):
            return WorkerRun(True, job.job_id, "unknown", "lease_ownership_lost")
