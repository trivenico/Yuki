"""Claim-fenced adapter for one-shot approval credential resolution.

This adapter composes a legacy worker-bound resolver with the durable task
engine's per-claim lease token. It checks the exact claim both before and after
credential resolution and discards a credential if the lease changes while
resolution is in progress.

It is a local dispatch guard, not a remote-side-effect fencing protocol.
"""
from __future__ import annotations

import re
from typing import Any, Callable


class LeaseBoundApprovalResolver:
    """Resolve an approval token only while one exact task claim is current."""

    def __init__(
        self,
        engine: Any,
        *,
        worker_id: str,
        resolver: Callable[[Any, str], str | None],
    ) -> None:
        if not callable(getattr(engine, "validate_lease", None)):
            raise TypeError("engine must provide validate_lease(task_id, worker_id, lease_token)")
        if not isinstance(worker_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", worker_id
        ):
            raise ValueError("worker_id must be a bounded trusted identity")
        if not callable(resolver):
            raise TypeError("resolver must be callable")
        resolver_worker = getattr(resolver, "worker_id", None)
        if resolver_worker is not None and resolver_worker != worker_id:
            raise PermissionError("approval resolver is bound to a different worker identity")
        self.engine = engine
        self.worker_id = worker_id
        self.resolver = resolver

    @staticmethod
    def _validate_claim_token(lease_token: str) -> None:
        if not isinstance(lease_token, str) or not 40 <= len(lease_token) <= 256:
            raise PermissionError("a valid durable task claim token is required")
        try:
            lease_token.encode("ascii")
        except UnicodeEncodeError:
            raise PermissionError("a valid durable task claim token is required") from None

    def _claim_is_current(self, task_id: str, lease_token: str) -> bool:
        try:
            return self.engine.validate_lease(task_id, self.worker_id, lease_token) is True
        except Exception:
            return False

    def resolve(
        self,
        context: Any,
        skill_name: str,
        *,
        lease_token: str,
    ) -> str | None:
        """Return a one-shot approval token only if the same claim survives lookup.

        A lease loss during lookup burns the one-shot credential but never
        releases it to a stale worker. The owner must issue a fresh approval.
        """
        # Avoid coupling this adapter to a concrete policy-context class: the
        # broker and worker layers own provenance construction. At this boundary
        # require a bounded task identifier before consulting the lease engine.
        try:
            task_id = context.task_id
        except Exception:
            raise PermissionError("trusted task provenance is required") from None
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            raise PermissionError("trusted task provenance is required")
        if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
            raise ValueError("skill_name is invalid")
        self._validate_claim_token(lease_token)
        if not self._claim_is_current(task_id, lease_token):
            raise PermissionError("durable task claim is no longer current")

        credential = self.resolver(context, skill_name)

        # The resolver may perform I/O or block. Re-check after it returns so a
        # credential is never handed to a worker whose lease changed mid-lookup.
        if not self._claim_is_current(task_id, lease_token):
            raise PermissionError("durable task claim changed during approval resolution")
        if credential is None:
            return None
        if not isinstance(credential, str) or not 40 <= len(credential) <= 256:
            raise PermissionError("approval resolver returned an invalid credential")
        try:
            credential.encode("ascii")
        except UnicodeEncodeError:
            raise PermissionError("approval resolver returned an invalid credential") from None
        return credential

    def execution_guard(self, task_id: str, lease_token: str) -> Callable[[], bool]:
        """Create a fail-closed guard for the final local handler dispatch."""
        self._validate_claim_token(lease_token)
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            raise ValueError("task_id is invalid")
        return lambda: self._claim_is_current(task_id, lease_token)
