"""Deterministic, privacy-aware provider routing over trusted registrations.

Routing consumes explicit readiness evidence and never performs health checks,
inference, retries, fallback requests, or provider construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from providers.provider_registry import ProviderRegistry

_ALLOWED_PRIVACY = frozenset({"local_only", "cloud_opt_in"})


@dataclass(frozen=True)
class ProviderRoutingDecision:
    provider_id: str
    privacy_class: str
    capabilities: tuple[str, ...]
    readiness_status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "privacy_class": self.privacy_class,
            "capabilities": list(self.capabilities),
            "readiness_status": self.readiness_status,
            "reason": self.reason,
        }


class ProviderRouter:
    """Choose one explicitly eligible provider, preferring local execution."""

    def __init__(self, registry: ProviderRegistry):
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("provider_registry_required")
        self.registry = registry

    def select(
        self, *, required_capabilities: set[str] | frozenset[str],
        readiness: Mapping[str, Mapping[str, Any]],
        privacy_policy: str = "local_only",
        cloud_opt_in: bool = False,
    ) -> ProviderRoutingDecision:
        if privacy_policy not in _ALLOWED_PRIVACY:
            raise ValueError("provider_privacy_policy_invalid")
        if type(cloud_opt_in) is not bool:
            raise TypeError("cloud_opt_in_must_be_boolean")
        if privacy_policy == "local_only" and cloud_opt_in:
            raise ValueError("cloud_opt_in_conflicts_with_local_only_policy")
        if not isinstance(readiness, Mapping):
            raise TypeError("provider_readiness_mapping_required")
        if not isinstance(required_capabilities, (set, frozenset)):
            raise TypeError("required_capabilities_must_be_a_set")

        eligible = []
        for item in self.registry.describe():
            provider_id = item["provider_id"]
            privacy = item["privacy_class"]
            if not required_capabilities <= set(item["capabilities"]):
                continue
            if privacy == "unknown":
                continue
            if privacy == "cloud_provider" and not (
                privacy_policy == "cloud_opt_in" and cloud_opt_in
            ):
                continue
            evidence = readiness.get(provider_id)
            if not isinstance(evidence, Mapping):
                continue
            if evidence.get("status") != "live_verified" or evidence.get("verification_level") != "live":
                continue
            if evidence.get("reachable") is not True:
                continue
            eligible.append((0 if privacy == "local" else 1, provider_id, privacy))

        if not eligible:
            raise LookupError("no_provider_satisfies_routing_policy")
        _, provider_id, privacy = sorted(eligible)[0]
        evidence = readiness[provider_id]
        return ProviderRoutingDecision(
            provider_id=provider_id,
            privacy_class=privacy,
            capabilities=tuple(sorted(required_capabilities)),
            readiness_status=str(evidence["status"]),
            reason="local_first" if privacy == "local" else "explicit_cloud_opt_in",
        )


__all__ = ["ProviderRouter", "ProviderRoutingDecision"]
