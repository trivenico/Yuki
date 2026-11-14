"""Typed provider outcome verification contracts for guarded tool executions.

Adapters must return evidence tied to the exact durable execution identity.
This module deliberately performs no retries and never treats a transport error
or missing provider record as proof that an action was not applied.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
import sys
import threading
import uuid
from typing import Any, Protocol, runtime_checkable


class ProviderOutcome(str, Enum):
    CONFIRMED_COMPLETED = "confirmed_completed"
    CONFIRMED_NOT_APPLIED = "confirmed_not_applied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderOutcomeEvidence:
    provider: str
    outcome: ProviderOutcome
    idempotency_key: str
    arguments_sha256: str
    evidence_sha256: str
    observed_at: str
    provider_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", self.provider):
            raise ValueError("provider must be a bounded provider identifier.")
        if not isinstance(self.outcome, ProviderOutcome):
            raise ValueError("outcome must be a ProviderOutcome.")
        for value, label in (
            (self.idempotency_key, "idempotency_key"),
            (self.arguments_sha256, "arguments_sha256"),
            (self.evidence_sha256, "evidence_sha256"),
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
        if not isinstance(self.observed_at, str) or len(self.observed_at) > 40:
            raise ValueError("observed_at must be an ISO-8601 UTC timestamp.")
        try:
            observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("observed_at must be an ISO-8601 UTC timestamp.") from None
        if observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(observed):
            raise ValueError("observed_at must include a UTC timezone.")
        # Permit small provider clock skew, but reject evidence dated materially
        # in the future; old observations remain valid for delayed reconciliation.
        from datetime import timedelta
        if observed > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("observed_at cannot be more than five minutes in the future.")
        if self.provider_reference is not None and (
            not isinstance(self.provider_reference, str)
            or not self.provider_reference.strip()
            or len(self.provider_reference) > 256
        ):
            raise ValueError("provider_reference must be bounded non-empty text when supplied.")


@runtime_checkable
class ProviderOutcomeAdapter(Protocol):
    """Explicit adapter contract; implementations query provider-owned status APIs."""

    def lookup_tool_outcome(self, execution: dict[str, Any]) -> ProviderOutcomeEvidence:
        """Return evidence for this exact execution, or UNKNOWN when inconclusive."""
        ...


def verify_provider_outcome(
    adapter: ProviderOutcomeAdapter, execution: dict[str, Any],
) -> ProviderOutcomeEvidence:
    """Query and bind evidence to the durable execution; never retry or infer success."""
    if not isinstance(execution, dict):
        raise TypeError("execution must be a mapping.")
    required = {"execution_id", "idempotency_key", "arguments_sha256", "status"}
    if not required.issubset(execution):
        raise ValueError("execution is missing durable identity fields.")
    if execution["status"] not in {"started", "uncertain"}:
        raise ValueError("provider lookup is only valid for started or uncertain executions.")
    lookup = getattr(adapter, "lookup_tool_outcome", None)
    if not callable(lookup):
        raise TypeError("provider adapter must explicitly implement lookup_tool_outcome.")
    evidence = lookup(dict(execution))
    if not isinstance(evidence, ProviderOutcomeEvidence):
        raise TypeError("provider adapter returned untyped outcome evidence.")
    if (evidence.idempotency_key != execution["idempotency_key"]
            or evidence.arguments_sha256 != execution["arguments_sha256"]):
        raise ValueError("provider evidence does not match the durable execution fingerprint.")
    return evidence


class ProviderOutcomeRegistry:
    """Explicit tool-name-to-adapter registry; no dynamic imports or fallbacks."""

    def __init__(self, adapters: dict[str, ProviderOutcomeAdapter]) -> None:
        if not isinstance(adapters, dict):
            raise TypeError("adapters must be an explicit mapping.")
        validated: dict[str, ProviderOutcomeAdapter] = {}
        for tool_name, adapter in adapters.items():
            if (not isinstance(tool_name, str)
                    or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", tool_name)):
                raise ValueError("adapter tool names must be bounded identifiers.")
            if not callable(getattr(adapter, "lookup_tool_outcome", None)):
                raise TypeError(f"adapter for {tool_name!r} has no lookup_tool_outcome method.")
            validated[tool_name] = adapter
        self._adapters = validated

    def lookup(self, tool_name: str, execution: dict[str, Any]) -> ProviderOutcomeEvidence:
        adapter = self._adapters.get(tool_name)
        if adapter is None:
            raise LookupError(f"no provider outcome adapter is registered for {tool_name!r}.")
        return verify_provider_outcome(adapter, execution)


class ProviderOutcomeReconciler:
    """Read-only provider lookup plus append-only evidence recording."""

    def __init__(
        self, store: Any, registry: ProviderOutcomeRegistry, *,
        lease_ttl_seconds: int = 120, lease_renew_interval_seconds: int = 30,
    ) -> None:
        required_store_methods = (
            "list_tool_executions", "acquire_provider_reconciliation_lease",
            "renew_provider_reconciliation_lease", "release_provider_reconciliation_lease",
            "record_provider_outcome_evidence",
        )
        if any(not callable(getattr(store, name, None)) for name in required_store_methods):
            raise TypeError("store does not implement the fenced provider reconciliation contract.")
        if not isinstance(registry, ProviderOutcomeRegistry):
            raise TypeError("registry must be a ProviderOutcomeRegistry.")
        if type(lease_ttl_seconds) is not int or not 5 <= lease_ttl_seconds <= 300:
            raise ValueError("lease_ttl_seconds must be an integer from 5 to 300.")
        if (type(lease_renew_interval_seconds) is not int
                or not 1 <= lease_renew_interval_seconds < lease_ttl_seconds):
            raise ValueError("lease_renew_interval_seconds must be positive and shorter than the lease TTL.")
        self._store = store
        self._registry = registry
        self._lease_ttl_seconds = lease_ttl_seconds
        self._lease_renew_interval_seconds = lease_renew_interval_seconds

    def reconcile(
        self, *, owner_id: str, conversation_id: str, request_id: str, call_id: str,
    ) -> dict[str, Any]:
        executions = self._store.list_tool_executions(
            owner_id=owner_id, conversation_id=conversation_id, request_id=request_id,
        )
        execution = next((item for item in executions if item["call_id"] == call_id), None)
        if execution is None:
            raise LookupError("tool execution not found.")
        lease_token = uuid.uuid4().hex
        self._store.acquire_provider_reconciliation_lease(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id, call_id=call_id, lease_token=lease_token,
            ttl_seconds=self._lease_ttl_seconds,
        )
        stop_renewal = threading.Event()
        lease_lost = threading.Event()

        def renew_claim() -> None:
            while not stop_renewal.wait(self._lease_renew_interval_seconds):
                try:
                    self._store.renew_provider_reconciliation_lease(
                        owner_id=owner_id, execution_id=execution["execution_id"],
                        lease_token=lease_token, ttl_seconds=self._lease_ttl_seconds,
                    )
                except Exception:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(
            target=renew_claim, name="yuki-provider-reconciliation-lease",
            daemon=True,
        )
        heartbeat_started = False
        try:
            heartbeat.start()
            heartbeat_started = True
            evidence = self._registry.lookup(
                execution["tool_name"],
                {
                    "execution_id": execution["execution_id"],
                    "idempotency_key": execution["idempotency_key"],
                    "arguments_sha256": execution["arguments_sha256"],
                    "status": execution["status"],
                },
            )
            stop_renewal.set()
            heartbeat.join()
            if lease_lost.is_set():
                from core.conversation_store import ConversationConflict
                raise ConversationConflict("provider reconciliation lease renewal failed.")
            return self._store.record_provider_outcome_evidence(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id=request_id, call_id=call_id, evidence=evidence,
                reconciliation_lease_token=lease_token,
            )
        finally:
            stop_renewal.set()
            if heartbeat_started:
                heartbeat.join()
            active_error = sys.exc_info()[1]
            try:
                self._store.release_provider_reconciliation_lease(
                    owner_id=owner_id, execution_id=execution["execution_id"],
                    lease_token=lease_token,
                )
            except BaseException as cleanup_error:
                if active_error is None:
                    raise
                add_note = getattr(active_error, "add_note", None)
                if callable(add_note):
                    add_note(f"Provider reconciliation lease cleanup also failed: {cleanup_error!r}")
