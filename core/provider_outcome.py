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
