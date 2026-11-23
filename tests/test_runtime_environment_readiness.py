import unittest
from datetime import datetime, timedelta, timezone

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.runtime_environment_readiness import (
    RuntimeEnvironmentEvidence, RuntimeEnvironmentRequirements,
    assess_runtime_environment,
)
from core.sandbox_executor import DockerSandboxExecutor, SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


class RuntimeEnvironmentReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.spec = AgentExecutionSpec(
            spec_id="runtime-check", role="reviewer", instructions="Review approved changes.",
            capabilities=("coding",), dependencies=("base-runtime",),
            budget=ExecutionBudget(max_runtime_seconds=120, max_memory_mb=256, max_tokens=1000, max_steps=5),
        )
        self.profile = SandboxExecutionProfile(
            spec_id="runtime-check", image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=120, memory_mb=256, cpu_millis=500).validate(),
            command=("python", "-m", "unittest"), workspace_resolver=lambda task: "/workspace",
        )
        self.requirements = RuntimeEnvironmentRequirements(
            runtime_versions={"python": "3.12.2"},
            locked_dependencies={"PyYAML": "6.0.3"}, minimum_disk_mb=512,
        )
        self.evidence = self.make_evidence()

    def make_evidence(self, **overrides):
        values = dict(
            task_id="task-1", spec_id="runtime-check", sandbox_image=self.profile.image,
            captured_at=self.now - timedelta(seconds=5), expires_at=self.now + timedelta(minutes=5),
            verifier_id="host-agent-1", runtime_versions={"python": "3.12.2"},
            installed_dependencies={"pyyaml": "6.0.3"}, cpu_millis=1000,
            memory_mb=512, disk_free_mb=2048, workspace_root_id="workspace-root",
            approved_workspace_root_id="workspace-root", workspace_within_approved_root=True,
            workspace_symlink_free=True, workspace_writable=True,
            sandbox_policy_sha256=DockerSandboxExecutor._policy_digest(self.profile.policy),
        )
        values.update(overrides)
        return RuntimeEnvironmentEvidence(**values)

    def assess(self, **overrides):
        values = dict(
            spec=self.spec, profile=self.profile, task_id="task-1",
            requirements=self.requirements, evidence=self.evidence,
            evidence_verifier=lambda evidence, spec, profile: True, now=self.now,
        )
        values.update(overrides)
        return assess_runtime_environment(**values)

    def test_matching_fresh_host_evidence_is_ready_only_for_approval(self):
        result = self.assess()
        self.assertEqual(result.status, "ready_for_approval")
        self.assertTrue(result.ready_for_approval)
        self.assertEqual(result.verifier_id, "host-agent-1")

    def test_missing_inputs_or_verifier_require_review(self):
        for field in ("spec", "profile", "requirements", "evidence", "evidence_verifier"):
            with self.subTest(field=field):
                result = self.assess(**{field: None})
                self.assertEqual(result.status, "review_required")
                self.assertFalse(result.ready_for_approval)

    def test_expected_worker_identity_is_bound(self):
        evidence = self.make_evidence(worker_id="worker-a")
        self.assertEqual(self.assess(evidence=evidence, expected_worker_id="worker-a").status, "ready_for_approval")
        result = self.assess(evidence=evidence, expected_worker_id="worker-b")
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("different worker" in reason for reason in result.reasons))

    def test_task_spec_image_and_policy_are_bound(self):
        for evidence in (
            self.make_evidence(task_id="other"),
            self.make_evidence(spec_id="other"),
            self.make_evidence(sandbox_image="other@sha256:" + "b" * 64),
            self.make_evidence(sandbox_policy_sha256="b" * 64),
        ):
            with self.subTest(evidence=evidence):
                self.assertEqual(self.assess(evidence=evidence).status, "blocked")

    def test_expired_future_and_overlong_evidence_is_blocked(self):
        for evidence in (
            self.make_evidence(expires_at=self.now - timedelta(seconds=1)),
            self.make_evidence(captured_at=self.now + timedelta(seconds=1)),
            self.make_evidence(expires_at=self.now + timedelta(hours=2)),
        ):
            with self.subTest(evidence=evidence):
                self.assertEqual(self.assess(evidence=evidence).status, "blocked")

    def test_dependency_and_runtime_mismatch_is_blocked(self):
        evidence = self.make_evidence(installed_dependencies={"PyYAML": "6.0.2"}, runtime_versions={"python": "3.11.0"})
        result = self.assess(evidence=evidence)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(result.reasons), 2)

    def test_operator_package_lock_is_independent_of_spec_graph_dependencies(self):
        requirements = RuntimeEnvironmentRequirements(
            runtime_versions={"python": "3.12.2"},
            locked_dependencies={"PyYAML": "6.0.3", "requests": "2.32.0"},
        )
        result = self.assess(requirements=requirements)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("requests" in reason for reason in result.reasons))

    def test_workspace_escape_symlink_and_write_failure_block(self):
        for changes in (
            {"workspace_within_approved_root": False},
            {"workspace_symlink_free": False},
            {"workspace_writable": False},
            {"approved_workspace_root_id": "different-root"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.assess(evidence=self.make_evidence(**changes)).status, "blocked")

    def test_resource_shortfalls_block(self):
        evidence = self.make_evidence(cpu_millis=100, memory_mb=128, disk_free_mb=10)
        result = self.assess(evidence=evidence)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(result.reasons), 3)

    def test_verifier_false_or_exception_fails_closed(self):
        self.assertEqual(self.assess(evidence_verifier=lambda *_: 1).status, "blocked")
        def fail(*_):
            raise RuntimeError("untrusted verifier")
        self.assertEqual(self.assess(evidence_verifier=fail).status, "blocked")

    def test_evidence_accepts_canonical_cross_ecosystem_inventory_above_legacy_limit(self):
        dependencies = {
            "PyYAML": "6.0.3",
            "deb:libssl3": "3.0.13",
            "npm:alpha#app/node_modules/alpha": "1.2.3",
        }
        dependencies.update({f"python-package-{index}": "1.0.0" for index in range(130)})
        evidence = self.make_evidence(installed_dependencies=dependencies)
        self.assertEqual(evidence.installed_dependencies["pyyaml"], "6.0.3")
        self.assertEqual(evidence.installed_dependencies["deb:libssl3"], "3.0.13")
        self.assertEqual(evidence.installed_dependencies["npm:alpha#app/node_modules/alpha"], "1.2.3")
        self.assertEqual(len(evidence.installed_dependencies), 133)

    def test_bad_numeric_and_timestamp_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            self.make_evidence(cpu_millis=True)
        with self.assertRaises(ValueError):
            self.make_evidence(expires_at=datetime.now())
        with self.assertRaises(ValueError):
            RuntimeEnvironmentRequirements(runtime_versions={}, locked_dependencies={}, minimum_disk_mb=0)


if __name__ == "__main__":
    unittest.main()
