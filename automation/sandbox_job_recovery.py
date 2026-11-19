"""Fail-closed restart reconciliation for durable Docker sandbox jobs."""
from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Callable

from automation.docker_job_runtime import DockerJobRuntime, DockerJobRuntimeError
from automation.sandbox_job_store import SandboxJobStore, SandboxJobStoreError


class SandboxJobRecoveryError(RuntimeError):
    """A durable job could not be safely reconciled after restart."""


class SandboxJobRecovery:
    """Reconcile only launch-ambiguous and running jobs after a host restart.

    This coordinator deliberately does not trust staging data or clean it up.
    It confirms the exact managed container is absent or stopped+removed before
    marking a job failed. Any ambiguous Docker or filesystem condition leaves
    the durable record and staging directory untouched.
    """

    def __init__(
        self,
        *,
        store: SandboxJobStore,
        runtime: DockerJobRuntime,
        jobs_root: str | Path,
        audit: Callable[[dict], None],
    ):
        if not isinstance(store, SandboxJobStore):
            raise TypeError("durable sandbox job store is required")
        if not callable(getattr(runtime, "handle_for_job", None)) or not callable(
            getattr(runtime, "reconcile", None)
        ):
            raise TypeError("runtime must provide deterministic handle reconstruction and reconciliation")
        if not callable(audit):
            raise TypeError("mandatory recovery audit callback is required")
        root = Path(jobs_root).expanduser()
        if root.is_symlink():
            raise ValueError("jobs root cannot be a symlink")
        root = root.resolve(strict=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("jobs root must be a directory owned by the service user")
        self._store = store
        self._runtime = runtime
        self._root = root
        self._audit = audit

    def _trusted_staging_path(self, record) -> Path:
        expected = self._root / record.job_id
        candidate = Path(record.staging_directory)
        if candidate != expected or candidate.parent != self._root:
            raise SandboxJobRecoveryError("durable staging path does not match job identity")
        try:
            info = candidate.lstat()
        except FileNotFoundError as exc:
            raise SandboxJobRecoveryError("durable staging directory is missing") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SandboxJobRecoveryError("staging path is not a real directory")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise SandboxJobRecoveryError("staging directory ownership or mode is unsafe")
        return candidate

    def recover_nonterminal(self) -> tuple[dict, ...]:
        """Reconcile launching/running jobs; preserve all ambiguous records."""
        outcomes = []
        for record in self._store.list_nonterminal():
            if record.state not in {"launching", "running"}:
                outcomes.append({"job_id": record.job_id, "state": record.state,
                                 "outcome": "deferred"})
                continue
            try:
                self._trusted_staging_path(record)
                expected_name = "yuki-job-" + record.job_id[4:]
                if record.container_name != expected_name:
                    raise SandboxJobRecoveryError("durable container identity is missing or mismatched")
                handle = self._runtime.handle_for_job(record.job_id)
                result = self._runtime.reconcile(handle)
                if result not in {"missing", "removed"}:
                    raise SandboxJobRecoveryError("runtime returned an unknown reconciliation result")
                self._audit({
                    "action": "sandbox.job.recovery",
                    "job_id": record.job_id,
                    "previous_state": record.state,
                    "outcome": result,
                })
                self._store.transition(
                    record.job_id,
                    expected_states=(record.state,),
                    new_state="failed",
                )
                outcomes.append({"job_id": record.job_id, "state": "failed",
                                 "outcome": result})
            except Exception as exc:
                # Do not mutate state or delete staging when any trust check,
                # runtime operation, audit, or compare-and-swap fails.
                outcomes.append({"job_id": record.job_id, "state": record.state,
                                 "outcome": "recovery_required",
                                 "error": type(exc).__name__})
        return tuple(outcomes)
