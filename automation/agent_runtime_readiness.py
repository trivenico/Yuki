"""Fail-closed policy for specialist model/provider runtime readiness.

This module evaluates evidence supplied by the real provider runtime manager.
It never constructs providers, performs inference, or treats configuration or
reachability as proof of live compatibility.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from providers.provider_readiness import ProviderReadiness


@dataclass(frozen=True)
class AgentRuntimeRequirement:
    provider: str
    model: str
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip() or len(self.provider) > 64:
            raise ValueError("required_provider_invalid")
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 120:
            raise ValueError("required_model_invalid")
        if not isinstance(self.capabilities, tuple) or len(self.capabilities) > 32:
            raise ValueError("required_capabilities_invalid")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 64 for item in self.capabilities):
            raise ValueError("required_capabilities_invalid")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("required_capabilities_duplicate")


@dataclass(frozen=True)
class AgentRuntimeReadiness:
    status: str
    provider: str | None
    model: str | None
    missing_capabilities: tuple[str, ...]
    verification_level: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_agent_runtime_readiness(
    *,
    enabled: bool,
    requirement: AgentRuntimeRequirement | None,
    provider_report: ProviderReadiness | None,
    configured_model: str | None,
) -> AgentRuntimeReadiness:
    """Return ready only when exact model/provider and live capability evidence match."""
    if type(enabled) is not bool:
        raise ValueError("agent_enabled_invalid")
    if not enabled:
        return AgentRuntimeReadiness("disabled", None, None, (), "none", "agent_disabled")
    if requirement is None:
        return AgentRuntimeReadiness(
            "requirements_not_declared", None, None, (), "none",
            "provider_and_model_requirements_not_declared",
        )
    if not isinstance(requirement, AgentRuntimeRequirement):
        raise TypeError("requirement_invalid")
    if provider_report is None:
        return AgentRuntimeReadiness(
            "not_configured", None, None, requirement.capabilities, "none",
            "provider_runtime_manager_unavailable",
        )
    if not isinstance(provider_report, ProviderReadiness):
        raise TypeError("provider_report_invalid")
    if provider_report.provider != requirement.provider:
        return AgentRuntimeReadiness(
            "provider_mismatch", provider_report.provider, configured_model, requirement.capabilities,
            provider_report.verification_level, "active_provider_does_not_match_requirement",
        )
    if configured_model != requirement.model:
        return AgentRuntimeReadiness(
            "model_mismatch", provider_report.provider, configured_model, requirement.capabilities,
            provider_report.verification_level, "active_model_does_not_match_requirement",
        )
    if provider_report.status != "live_verified" or not provider_report.live_verified:
        return AgentRuntimeReadiness(
            provider_report.status, provider_report.provider, configured_model, requirement.capabilities,
            provider_report.verification_level, "provider_has_no_passing_live_conformance_evidence",
        )
    missing = tuple(sorted(
        capability for capability in requirement.capabilities
        if provider_report.capabilities.get(capability) is not True
    ))
    if missing:
        return AgentRuntimeReadiness(
            "capability_mismatch", provider_report.provider, configured_model, missing,
            provider_report.verification_level, "required_provider_capabilities_not_verified",
        )
    return AgentRuntimeReadiness(
        "ready", provider_report.provider, configured_model, (), "live",
        "provider_model_and_capabilities_live_verified",
    )


__all__ = [
    "AgentRuntimeRequirement", "AgentRuntimeReadiness",
    "evaluate_agent_runtime_readiness",
]
