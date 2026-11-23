import unittest
from datetime import datetime, timedelta, timezone

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.complete_execution_readiness import assess_complete_execution_readiness
from core.execution_contract_readiness import WorkerExecutionCapacity
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence, RuntimeEnvironmentRequirements
from core.sandbox_executor import DockerSandboxExecutor, SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


class CompleteExecutionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.spec = AgentExecutionSpec(
            spec_id="complete-check", role="reviewer", instructions="Review the change.",
            capabilities=("coding",), dependencies=("pyyaml",),
            budget=ExecutionBudget(max_runtime_seconds=120, max_memory_mb=256, max_tokens=1000, max_steps=5),
        )
        self.profile = SandboxExecutionProfile(
            spec_id="complete-check", image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=120, memory_mb=256, cpu_millis=500).validate(),
            command=("python", "-m", "unittest"), workspace_resolver=lambda task: "/workspace",
        )
        self.capacity = WorkerExecutionCapacity(120, 256, 1000, 5)
        self.requirements = RuntimeEnvironmentRequirements(
            runtime_versions={"python": "3.12.2"}, locked_dependencies={"pyyaml": "6.0.3"},
        )
        self.evidence = RuntimeEnvironmentEvidence(
            task_id="task-1", spec_id="complete-check", sandbox_image=self.profile.image,
            captured_at=self.now - timedelta(seconds=5), expires_at=self.now + timedelta(minutes=5),
            verifier_id="host-agent", runtime_versions={"python": "3.12.2"},
            installed_dependencies={"pyyaml": "6.0.3"}, cpu_millis=1000, memory_mb=512,
            disk_free_mb=2048, workspace_root_id="root", approved_workspace_root_id="root",
            workspace_within_approved_root=True, workspace_symlink_free=True, workspace_writable=True,
            sandbox_policy_sha256=DockerSandboxExecutor._policy_digest(self.profile.policy),
        )

    def assess(self, **overrides):
        values = dict(
            spec=self.spec, profile=self.profile, worker_capacity=self.capacity,
            sandbox_verified=True, task_id="task-1", runtime_requirements=self.requirements,
            runtime_evidence=self.evidence,
            runtime_evidence_verifier=lambda evidence, spec, profile: True, now=self.now,
        )
        values.update(overrides)
        return assess_complete_execution_readiness(**values)

    def test_both_preflights_must_pass(self):
        result = self.assess()
        self.assertEqual(result.status, "ready_for_approval")
        self.assertTrue(result.ready_for_approval)
        self.assertEqual(result.contract_status, "ready_for_approval")
        self.assertEqual(result.environment_status, "ready_for_approval")

    def test_missing_environment_evidence_cannot_be_overridden_by_contract(self):
        result = self.assess(runtime_evidence=None)
        self.assertEqual(result.status, "review_required")
        self.assertFalse(result.ready_for_approval)
        self.assertEqual(result.contract_status, "ready_for_approval")
        self.assertEqual(result.environment_status, "review_required")

    def test_failed_sandbox_or_environment_blocks_composite(self):
        result = self.assess(sandbox_verified=False)
        self.assertEqual(result.status, "blocked")
        result = self.assess(runtime_evidence_verifier=lambda *_: False)
        self.assertEqual(result.status, "blocked")

    def test_no_approval_or_dispatch_side_effects(self):
        result = self.assess()
        self.assertFalse(hasattr(result, "approve"))
        self.assertFalse(hasattr(result, "dispatch"))


if __name__ == "__main__":
    unittest.main()
