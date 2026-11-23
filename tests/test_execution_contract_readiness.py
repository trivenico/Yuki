import unittest

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.execution_contract_readiness import (
    WorkerExecutionCapacity, assess_execution_contract,
)
from core.sandbox_executor import SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


class ExecutionContractReadinessTests(unittest.TestCase):
    def setUp(self):
        self.spec = AgentExecutionSpec(
            spec_id="code-review", role="reviewer", instructions="Review the approved change.",
            capabilities=("coding",),
            budget=ExecutionBudget(
                max_runtime_seconds=300, max_memory_mb=512,
                max_tokens=8000, max_steps=20,
            ),
        )
        self.profile = SandboxExecutionProfile(
            spec_id="code-review",
            image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(
                timeout_seconds=240, memory_mb=384,
            ).validate(),
            command=("python", "-m", "unittest"),
            workspace_resolver=lambda task: "/workspace",
        )
        self.capacity = WorkerExecutionCapacity(
            max_runtime_seconds=600, max_memory_mb=1024,
            max_tokens=16000, max_steps=20,
        )

    def assess(self, **overrides):
        values = {
            "spec": self.spec, "profile": self.profile,
            "worker_capacity": self.capacity, "sandbox_verified": True,
        }
        values.update(overrides)
        return assess_execution_contract(**values)

    def test_matching_verified_contract_is_only_ready_for_approval(self):
        result = self.assess()
        self.assertEqual(result.status, "ready_for_approval")
        self.assertTrue(result.ready_for_approval)
        self.assertEqual(result.sandbox_image, self.profile.image)

    def test_missing_spec_profile_capacity_or_attestation_requires_review(self):
        for field in ("spec", "profile", "worker_capacity", "sandbox_verified"):
            with self.subTest(field=field):
                result = self.assess(**{field: None})
                self.assertEqual(result.status, "review_required")
                self.assertFalse(result.ready_for_approval)

    def test_failed_sandbox_attestation_blocks(self):
        result = self.assess(sandbox_verified=False)
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.ready_for_approval)

    def test_profile_must_match_spec_identity(self):
        wrong = SandboxExecutionProfile(
            spec_id="other-spec", image=self.profile.image,
            policy=self.profile.policy, command=self.profile.command,
            workspace_resolver=self.profile.workspace_resolver,
        )
        result = self.assess(profile=wrong)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("does not match" in reason for reason in result.reasons))

    def test_profile_limits_cannot_exceed_spec_budget(self):
        oversized = SandboxExecutionProfile(
            spec_id="code-review", image=self.profile.image,
            policy=SandboxPolicy(timeout_seconds=360, memory_mb=1024).validate(),
            command=self.profile.command,
            workspace_resolver=self.profile.workspace_resolver,
        )
        result = self.assess(profile=oversized)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(result.reasons), 2)

    def test_worker_capacity_must_cover_every_requested_budget(self):
        limited = WorkerExecutionCapacity(
            max_runtime_seconds=100, max_memory_mb=256,
            max_tokens=1000, max_steps=5,
        )
        result = self.assess(worker_capacity=limited)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(result.reasons), 4)

    def test_no_dispatch_or_approval_side_effects_are_exposed(self):
        result = self.assess()
        self.assertFalse(hasattr(result, "dispatch"))
        self.assertFalse(hasattr(result, "approve"))

    def test_invalid_attestation_type_is_rejected(self):
        with self.assertRaises(ValueError):
            self.assess(sandbox_verified=1)


if __name__ == "__main__":
    unittest.main()
