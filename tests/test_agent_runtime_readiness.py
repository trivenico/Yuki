import unittest

from automation.agent_runtime_readiness import (
    AgentRuntimeRequirement, evaluate_agent_runtime_readiness,
)
from providers.provider_readiness import ProviderReadiness


def report(*, status="live_verified", live=True, capabilities=None):
    return ProviderReadiness(
        provider="ollama", status=status, configured=True, reachable=True,
        live_verified=live, capabilities=capabilities or {"chat": True},
        verification_level="live" if live else "availability_only",
        detail="test evidence object",
    )


class AgentRuntimeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.requirement = AgentRuntimeRequirement("ollama", "qwen2.5:7b", ("chat",))

    def test_ready_requires_exact_provider_model_and_live_capability_evidence(self):
        result = evaluate_agent_runtime_readiness(
            enabled=True, requirement=self.requirement,
            provider_report=report(), configured_model="qwen2.5:7b",
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.verification_level, "live")

    def test_disabled_agent_never_reports_ready(self):
        result = evaluate_agent_runtime_readiness(
            enabled=False, requirement=self.requirement,
            provider_report=report(), configured_model="qwen2.5:7b",
        )
        self.assertEqual(result.status, "disabled")

    def test_missing_requirements_are_not_assumed_ready(self):
        result = evaluate_agent_runtime_readiness(
            enabled=True, requirement=None, provider_report=report(),
            configured_model="qwen2.5:7b",
        )
        self.assertEqual(result.status, "requirements_not_declared")

    def test_missing_provider_runtime_fails_closed(self):
        result = evaluate_agent_runtime_readiness(
            enabled=True, requirement=self.requirement,
            provider_report=None, configured_model=None,
        )
        self.assertEqual(result.status, "not_configured")

    def test_provider_and_model_mismatch_are_rejected(self):
        wrong_provider = ProviderReadiness(
            provider="openai", status="live_verified", configured=True,
            reachable=True, live_verified=True, capabilities={"chat": True},
            verification_level="live", detail="evidence",
        )
        self.assertEqual(evaluate_agent_runtime_readiness(
            enabled=True, requirement=self.requirement,
            provider_report=wrong_provider, configured_model="qwen2.5:7b",
        ).status, "provider_mismatch")
        self.assertEqual(evaluate_agent_runtime_readiness(
            enabled=True, requirement=self.requirement,
            provider_report=report(), configured_model="different-model",
        ).status, "model_mismatch")

    def test_reachability_or_configuration_is_not_live_verification(self):
        result = evaluate_agent_runtime_readiness(
            enabled=True, requirement=self.requirement,
            provider_report=report(status="reachable_unverified", live=False),
            configured_model="qwen2.5:7b",
        )
        self.assertEqual(result.status, "reachable_unverified")

    def test_missing_capability_blocks_readiness(self):
        result = evaluate_agent_runtime_readiness(
            enabled=True, requirement=self.requirement,
            provider_report=report(capabilities={"chat": False}),
            configured_model="qwen2.5:7b",
        )
        self.assertEqual(result.status, "capability_mismatch")
        self.assertEqual(result.missing_capabilities, ("chat",))


if __name__ == "__main__":
    unittest.main()
