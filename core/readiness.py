"""Truthful capability and release-readiness reporting for Yuki.

This module reports configuration and supplied verification evidence. It never
runs health checks itself and never labels an integration healthy merely because
a module or configuration flag exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Optional


class CapabilityState(str, Enum):
    AVAILABLE = "available"
    DISABLED = "disabled"
    NEEDS_CONFIGURATION = "needs_configuration"
    NOT_IMPLEMENTED = "not_implemented"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Capability:
    name: str
    area: str
    state: CapabilityState
    evidence: str
    risk: str = "low"


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ReadinessReport:
    generated_for: str
    capabilities: tuple[Capability, ...]
    release_gates: tuple[Gate, ...]
    production_ready: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_for": self.generated_for,
            "capabilities": [
                {**asdict(item), "state": item.state.value} for item in self.capabilities
            ],
            "release_gates": [asdict(item) for item in self.release_gates],
            "production_ready": self.production_ready,
            "blockers": list(self.blockers),
        }


REQUIRED_RELEASE_GATES = (
    "automated_tests",
    "security_review",
    "backup_restore_test",
    "access_control_review",
    "monitoring_and_alerting",
    "integration_smoke_test",
)


def build_readiness_report(
    config: Mapping[str, Any],
    *,
    verified_gates: Optional[Mapping[str, str]] = None,
    runtime_health: Optional[Mapping[str, bool]] = None,
) -> ReadinessReport:
    """Build a conservative report from config plus explicit external evidence."""
    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")
    verified_gates = verified_gates or {}
    runtime_health = runtime_health or {}

    capabilities = [
        Capability(
            "model_provider", "intelligence",
            CapabilityState.NEEDS_CONFIGURATION if config.get("provider", "mock") == "mock"
            else CapabilityState.UNVERIFIED,
            "Provider selection is configured; successful inference has not been proven by this report.",
        ),
        Capability(
            "registered_skills", "tools", CapabilityState.AVAILABLE,
            "A skill registry exists; individual integrations and permissions need separate validation.",
            "medium",
        ),
        Capability(
            "plugin_discovery", "extensibility",
            CapabilityState.AVAILABLE if config.get("plugin_discovery_enabled") else CapabilityState.DISABLED,
            "Python plugin discovery executes code with Yuki process privileges; disabled by default.",
            "high",
        ),
        Capability(
            "voice", "interface",
            CapabilityState.UNVERIFIED if config.get("voice_enabled") else CapabilityState.DISABLED,
            "Voice requires an explicit hardware and model integration smoke test.", "medium",
        ),
        Capability(
            "wake_word", "interface",
            CapabilityState.UNVERIFIED if config.get("wakeword_enabled") else CapabilityState.DISABLED,
            "Configuration does not prove model availability, microphone operation, or false-accept performance.",
            "medium",
        ),
        Capability(
            "web_research", "research",
            CapabilityState.AVAILABLE if config.get("web_allowed_hosts") else CapabilityState.NEEDS_CONFIGURATION,
            "An allowlist is configured only when web_allowed_hosts is non-empty; provider reliability still needs testing.",
            "medium",
        ),
        Capability(
            "messaging_gateway", "channels", CapabilityState.NOT_IMPLEMENTED,
            "A production channel gateway is not verified by this report.", "high",
        ),
        Capability(
            "mcp_interoperability", "integrations", CapabilityState.UNVERIFIED,
            "A restricted client primitive exists; end-to-end auth, timeout, and revocation tests are required.",
            "high",
        ),
        Capability(
            "a2a_agent_interoperability", "integrations", CapabilityState.NOT_IMPLEMENTED,
            "Agent discovery, identity, delegation, and task lifecycle are not verified as implemented.",
            "high",
        ),
        Capability(
            "durable_scheduling", "automation", CapabilityState.UNVERIFIED,
            "Process-bound scheduling is not equivalent to restart-safe durable scheduling.", "medium",
        ),
        Capability(
            "persistent_memory", "memory", CapabilityState.AVAILABLE,
            "Local memory exists; backup, restore, retention, and concurrency require separate verification.",
            "medium",
        ),
        Capability(
            "observability", "operations", CapabilityState.UNVERIFIED,
            "Production metrics, alerting, trace correlation, and secret-redaction tests are not evidenced here.",
            "medium",
        ),
    ]

    gates = []
    blockers = []
    for name in REQUIRED_RELEASE_GATES:
        evidence = verified_gates.get(name, "")
        passed = isinstance(evidence, str) and bool(evidence.strip())
        gates.append(Gate(name, passed, evidence.strip()[:500] if passed else "No verification evidence supplied."))
        if not passed:
            blockers.append(f"Release gate not verified: {name}")

    for capability in capabilities:
        if capability.state in {CapabilityState.NOT_IMPLEMENTED, CapabilityState.NEEDS_CONFIGURATION}:
            blockers.append(f"{capability.name}: {capability.state.value}")
    for name, healthy in runtime_health.items():
        if healthy is False:
            blockers.append(f"Runtime health check failed: {name}")

    return ReadinessReport(
        generated_for="Yuki",
        capabilities=tuple(capabilities),
        release_gates=tuple(gates),
        production_ready=not blockers and all(gate.passed for gate in gates),
        blockers=tuple(dict.fromkeys(blockers)),
    )
