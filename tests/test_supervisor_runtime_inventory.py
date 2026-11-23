import unittest
from datetime import datetime, timedelta, timezone

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence
from core.sandbox_executor import DockerSandboxExecutor, SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy
from core.supervisor_runtime_inventory import (
    SupervisorRuntimeInventoryAdapter, SupervisorRuntimeSnapshot,
)
from core.host_runtime_inventory import RuntimeInventoryError


class SupervisorRuntimeInventoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.spec = AgentExecutionSpec(
            spec_id="supervisor-inventory", role="reviewer", instructions="Inspect approved work.",
            capabilities=("coding",), dependencies=(),
            budget=ExecutionBudget(max_runtime_seconds=60, max_memory_mb=128, max_tokens=500, max_steps=3),
        )
        self.profile = SandboxExecutionProfile(
            spec_id=self.spec.spec_id, image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=60, memory_mb=128, cpu_millis=250).validate(),
            command=("python", "-m", "unittest"), workspace_resolver=lambda task: "/workspace",
        )
        now = datetime.now(timezone.utc)
        self.policy_digest = DockerSandboxExecutor._policy_digest(self.profile.policy)
        evidence = RuntimeEnvironmentEvidence(
            task_id="task-9", spec_id=self.spec.spec_id, sandbox_image=self.profile.image,
            captured_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=2),
            verifier_id="supervisor-attestor", runtime_versions={"python": "3.12.2"},
            installed_dependencies={"requests": "2.32.3"}, cpu_millis=500,
            memory_mb=256, disk_free_mb=2048, workspace_root_id="approved-root",
            approved_workspace_root_id="approved-root", workspace_within_approved_root=True,
            workspace_symlink_free=True, workspace_writable=True,
            sandbox_policy_sha256=self.policy_digest, worker_id="worker-session-9",
        )
        self.snapshot = SupervisorRuntimeSnapshot(
            evidence=evidence, host_id="host-1", container_id="container-123",
            worker_session_id="worker-session-9", observed_image_digest="sha256:" + "a" * 64,
            observed_policy_sha256=self.policy_digest,
        )

    def test_maps_only_authenticated_supervisor_snapshot(self):
        verified = []
        adapter = SupervisorRuntimeInventoryAdapter(
            collector=lambda *_: self.snapshot,
            verifier=lambda snapshot, worker, spec, profile: verified.append(snapshot.container_id) or True,
        )
        result = adapter.collect(
            task_id="task-9", worker_id="worker-session-9", spec=self.spec, profile=self.profile,
        )
        self.assertIs(result, self.snapshot.evidence)
        self.assertEqual(verified, ["container-123"])

    def test_rejects_image_policy_and_session_mismatch_before_verification(self):
        for field, value in (
            ("observed_image_digest", "sha256:" + "b" * 64),
            ("observed_policy_sha256", "b" * 64),
            ("worker_session_id", "other-session"),
        ):
            with self.subTest(field=field):
                altered = dict(
                    evidence=self.snapshot.evidence, host_id=self.snapshot.host_id,
                    container_id=self.snapshot.container_id,
                    worker_session_id=self.snapshot.worker_session_id,
                    observed_image_digest=self.snapshot.observed_image_digest,
                    observed_policy_sha256=self.snapshot.observed_policy_sha256,
                )
                altered[field] = value
                snapshot = SupervisorRuntimeSnapshot(**altered)
                adapter = SupervisorRuntimeInventoryAdapter(
                    collector=lambda *_: snapshot, verifier=lambda *_: True,
                )
                with self.assertRaises(RuntimeInventoryError):
                    adapter.collect(
                        task_id="task-9", worker_id="worker-session-9",
                        spec=self.spec, profile=self.profile,
                    )


    def test_binds_snapshot_to_exact_container_from_sandbox_lifecycle(self):
        requested = []
        adapter = SupervisorRuntimeInventoryAdapter(
            collector=lambda task, worker, container, spec, profile: requested.append(container) or self.snapshot,
            verifier=lambda *_: True,
        )
        result = adapter.collect(
            task_id="task-9", worker_id="worker-session-9", spec=self.spec,
            profile=self.profile, expected_container_id="container-123",
        )
        self.assertIs(result, self.snapshot.evidence)
        self.assertEqual(requested, ["container-123"])
        with self.assertRaises(RuntimeInventoryError):
            adapter.collect(
                task_id="task-9", worker_id="worker-session-9", spec=self.spec,
                profile=self.profile, expected_container_id="different-container",
            )

    def test_verifier_must_return_exact_true(self):
        for verifier in (lambda *_: False, lambda *_: 1, lambda *_: (_ for _ in ()).throw(RuntimeError())):
            adapter = SupervisorRuntimeInventoryAdapter(collector=lambda *_: self.snapshot, verifier=verifier)
            with self.assertRaises(RuntimeInventoryError):
                adapter.collect(
                    task_id="task-9", worker_id="worker-session-9",
                    spec=self.spec, profile=self.profile,
                )

    def test_rejects_untyped_snapshot_and_invalid_container_identity(self):
        adapter = SupervisorRuntimeInventoryAdapter(collector=lambda *_: self.snapshot.evidence, verifier=lambda *_: True)
        with self.assertRaises(RuntimeInventoryError):
            adapter.collect(task_id="task-9", worker_id="worker-session-9", spec=self.spec, profile=self.profile)
        with self.assertRaises(ValueError):
            SupervisorRuntimeSnapshot(
                evidence=self.snapshot.evidence, host_id="host", container_id="../escape",
                worker_session_id="worker-session-9",
                observed_image_digest=self.snapshot.observed_image_digest,
                observed_policy_sha256=self.policy_digest,
            )


if __name__ == "__main__":
    unittest.main()
