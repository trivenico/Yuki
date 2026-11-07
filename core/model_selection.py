"""Digest-bound, evidence-based selection of installed local models.

This layer never activates a provider. It ranks only exact-digest benchmark
profiles and returns an approval-gated recommendation for an operator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from core.local_model_registry import LocalModel, LocalModelRegistry
from core.model_capabilities import ModelCapabilityRegistry


@dataclass(frozen=True)
class BenchmarkEvidence:
    model_id: str
    model_digest: str
    capability: str
    score: float
    suite: str
    suite_version: str
    evaluated_on: str
    evidence_reference: str

    def validate(self) -> None:
        if not self.model_id.strip() or not self.model_digest.strip():
            raise ValueError("benchmark evidence must bind to model ID and digest.")
        if not self.capability.strip() or not 0 <= self.score <= 1:
            raise ValueError("invalid capability or score.")
        if not self.suite.strip() or not self.suite_version.strip():
            raise ValueError("benchmark suite and version are required.")
        date.fromisoformat(self.evaluated_on)
        if not self.evidence_reference.startswith(("https://", "file://")):
            raise ValueError("benchmark evidence reference must be auditable.")


@dataclass(frozen=True)
class LocalModelCandidate:
    model_name: str
    digest: str
    status: str
    benchmark_score: float | None
    estimated_memory_gib: float | None
    rationale: str


def select_local_models(
    role: str,
    models: Iterable[LocalModel],
    capability_registry: ModelCapabilityRegistry,
    benchmark_evidence: Iterable[BenchmarkEvidence],
    *,
    available_memory_gib: float | None,
    minimum_score: float = 0.70,
) -> list[LocalModelCandidate]:
    """Return ranked candidates; never changes provider selection or downloads."""
    if not isinstance(role, str) or not role.strip():
        raise ValueError("role is required.")
    if not 0 <= minimum_score <= 1:
        raise ValueError("minimum_score must be between 0 and 1.")
    if available_memory_gib is not None and available_memory_gib <= 0:
        raise ValueError("available_memory_gib must be positive or None.")

    model_list = list(models)
    memory = {
        item.model_name: item
        for item in LocalModelRegistry.recommend(
            model_list, available_memory_gib=available_memory_gib
        )
    }
    evidence_by_key = {}
    for evidence in benchmark_evidence:
        evidence.validate()
        key = (evidence.model_id.casefold(), evidence.model_digest, evidence.capability)
        if key in evidence_by_key:
            raise ValueError("duplicate benchmark evidence for model digest and capability.")
        evidence_by_key[key] = evidence

    candidates = []
    for model in model_list:
        capability = capability_registry.get(model.name)
        estimate = memory[model.name].estimated_memory_gib
        if capability is None or not capability.verified or capability.runtime_status != "verified":
            candidates.append(LocalModelCandidate(
                model.name, model.digest or "", "capability_unverified", None, estimate,
                "No reviewed, runtime-verified capability record exists for this model.",
            ))
            continue
        if not model.digest or capability.model_digest != model.digest:
            candidates.append(LocalModelCandidate(
                model.name, model.digest or "", "digest_mismatch", None, estimate,
                "Capability record is not bound to the exact installed model digest.",
            ))
            continue
        evidence = evidence_by_key.get((model.name.casefold(), model.digest, role))
        if evidence is None:
            candidates.append(LocalModelCandidate(
                model.name, model.digest, "benchmark_missing", None, estimate,
                "No benchmark evidence exists for this role and exact model digest.",
            ))
            continue
        if estimate is None or available_memory_gib is None:
            status, rationale = "hardware_unknown", "Available memory or model metadata is insufficient to estimate fit."
        elif estimate > available_memory_gib * 0.70:
            status, rationale = "unlikely_to_fit", "Estimated memory exceeds the conservative 70% budget."
        elif evidence.score < minimum_score:
            status, rationale = "below_threshold", "Benchmark score is below the configured role threshold."
        else:
            status, rationale = "candidate", "Exact-digest capability and benchmark evidence meet the role and memory thresholds."
        candidates.append(LocalModelCandidate(
            model.name, model.digest, status, evidence.score, estimate,
            rationale + " This is a recommendation only; live performance and context-dependent fit are not guaranteed.",
        ))

    priority = {"candidate": 0, "capability_unverified": 1, "digest_mismatch": 2,
                "benchmark_missing": 3, "hardware_unknown": 4,
                "below_threshold": 5, "unlikely_to_fit": 6}
    return sorted(candidates, key=lambda item: (
        priority[item.status], -(item.benchmark_score or 0), item.model_name.casefold()
    ))
