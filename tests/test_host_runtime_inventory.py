import unittest
from datetime import datetime, timedelta, timezone

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.host_runtime_inventory import HostRuntimeInventoryProvider, RuntimeInventoryError
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence
from core.sandbox_executor import DockerSandboxExecutor, SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


class HostRuntimeInventoryProviderTests(unittest.TestCase):
    def setUp(self):
        self.spec = AgentExecutionSpec(
            spec_id="host-inventory", role="reviewer", instructions="Inspect approved work.",
            capabilities=("coding",), dependencies=(),
            budget=ExecutionBudget(max_runtime_seconds=60, max_memory_mb=128, max_tokens=500, max_steps=3),
        )
        self.profile = SandboxExecutionProfile(
            spec_id="host-inventory", image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=60, memory_mb=128, cpu_millis=250).validate(),
            command=("python", "-m", "unittest"), workspace_resolver=lambda task: "/workspace",
        )
        now = datetime.now(timezone.utc)
        self.evidence = RuntimeEnvironmentEvidence(
            task_id="task-7", spec_id=self.spec.spec_id, sandbox_image=self.profile.image,
            captured_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=2),
            verifier_id="supervisor-1", runtime_versions={"python": "3.12.2"},
            installed_dependencies={}, cpu_millis=500, memory_mb=256, disk_free_mb=1024,
            workspace_root_id="root-1", approved_workspace_root_id="root-1",
            workspace_within_approved_root=True, workspace_symlink_free=True,
            workspace_writable=True,
            sandbox_policy_sha256=DockerSandboxExecutor._policy_digest(self.profile.policy),
            worker_id="worker-session-3",
        )

    def test_collects_only_matching_host_verified_evidence(self):
        calls = []
        provider = HostRuntimeInventoryProvider(
            collector=lambda task, worker, spec, profile: self.evidence,
            verifier=lambda evidence, worker, spec, profile: calls.append(worker) or True,
        )
        result = provider.collect(
            task_id="task-7", worker_id="worker-session-3",
            spec=self.spec, profile=self.profile,
        )
        self.assertIs(result, self.evidence)
        self.assertEqual(calls, ["worker-session-3"])

    def test_rejects_worker_task_spec_and_image_mismatch(self):
        provider = HostRuntimeInventoryProvider(
            collector=lambda *_: self.evidence, verifier=lambda *_: True,
        )
        for task, worker in (("other-task", "worker-session-3"), ("task-7", "other-worker")):
            with self.subTest(task=task, worker=worker):
                with self.assertRaises(RuntimeInventoryError):
                    provider.collect(task_id=task, worker_id=worker, spec=self.spec, profile=self.profile)

    def test_verifier_false_or_exception_fails_closed(self):
        for verifier in (lambda *_: False, lambda *_: (_ for _ in ()).throw(RuntimeError("no"))):
            provider = HostRuntimeInventoryProvider(collector=lambda *_: self.evidence, verifier=verifier)
            with self.assertRaises(RuntimeInventoryError):
                provider.collect(task_id="task-7", worker_id="worker-session-3", spec=self.spec, profile=self.profile)

    def test_collector_failure_and_untyped_result_fail_closed(self):
        for collector in (
            lambda *_: (_ for _ in ()).throw(OSError("host unavailable")),
            lambda *_: {"python": "3.12.2"},
        ):
            provider = HostRuntimeInventoryProvider(collector=collector, verifier=lambda *_: True)
            with self.assertRaises(RuntimeInventoryError):
                provider.collect(task_id="task-7", worker_id="worker-session-3", spec=self.spec, profile=self.profile)

    def test_profile_mismatch_and_invalid_worker_are_rejected(self):
        provider = HostRuntimeInventoryProvider(collector=lambda *_: self.evidence, verifier=lambda *_: True)
        wrong_profile = SandboxExecutionProfile(
            spec_id="different", image=self.profile.image, policy=self.profile.policy,
            command=self.profile.command, workspace_resolver=self.profile.workspace_resolver,
        )
        with self.assertRaises(RuntimeInventoryError):
            provider.collect(task_id="task-7", worker_id="worker-session-3", spec=self.spec, profile=wrong_profile)
        with self.assertRaises(ValueError):
            provider.collect(task_id="task-7", worker_id="", spec=self.spec, profile=self.profile)


if __name__ == "__main__":
    unittest.main()
