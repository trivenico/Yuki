import unittest
from datetime import datetime, timedelta, timezone

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.docker_engine_supervisor import DockerEngineAPI, DockerEngineSupervisorCollector
from core.host_runtime_inventory import RuntimeInventoryError
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence
from core.sandbox_executor import DockerSandboxExecutor, SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


IMAGE_DIGEST = "sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
CONTAINER_NAME = "yuki-" + "c" * 32


class FakeDockerAPI(DockerEngineAPI):
    def __init__(self):
        self.container = {
            "Id": "d" * 64,
            "Name": "/" + CONTAINER_NAME,
            "Image": IMAGE_ID,
            "State": {"Running": True},
            "Config": {"Labels": {
                "yuki.managed_by": "yuki",
                "yuki.task_id": "task-1",
                "yuki.worker_id": "worker-1",
                "yuki.owner_sha256": "e" * 64,
                "yuki.profile_id": "docker-inspect",
                "yuki.image_digest": IMAGE_DIGEST,
            }},
            "HostConfig": {"Memory": 128 * 1024 * 1024, "NanoCpus": 250_000_000},
        }
        self.image = {"RepoDigests": ["python@" + IMAGE_DIGEST]}

    def get_json(self, path):
        if path == f"/containers/{CONTAINER_NAME}/json":
            return self.container
        if path == f"/images/{IMAGE_ID}/json":
            return self.image
        raise AssertionError(path)


class DockerEngineSupervisorCollectorTests(unittest.TestCase):
    def setUp(self):
        self.spec = AgentExecutionSpec(
            spec_id="docker-inspect", role="reviewer", instructions="Inspect safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=200, max_steps=2,
            ),
        )
        self.profile = SandboxExecutionProfile(
            spec_id=self.spec.spec_id, image="python@" + IMAGE_DIGEST,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250),
            command=("python", "--version"), workspace_resolver=lambda _: "/workspace",
        )
        now = datetime.now(timezone.utc)
        self.evidence = RuntimeEnvironmentEvidence(
            task_id="task-1", spec_id=self.spec.spec_id, sandbox_image=self.profile.image,
            captured_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=2),
            verifier_id="host-agent", runtime_versions={"python": "3.12.2"},
            installed_dependencies={}, cpu_millis=250, memory_mb=128, disk_free_mb=1024,
            workspace_root_id="root-1", approved_workspace_root_id="root-1",
            workspace_within_approved_root=True, workspace_symlink_free=True,
            workspace_writable=True,
            sandbox_policy_sha256=DockerSandboxExecutor._policy_digest(self.profile.policy),
            worker_id="worker-1",
        )
        self.api = FakeDockerAPI()
        self.collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            evidence_builder=lambda *_: self.evidence,
        )

    def collect(self):
        return self.collector(
            "task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile,
        )

    def test_binds_live_container_labels_digest_and_policy(self):
        snapshot = self.collect()
        self.assertEqual(snapshot.container_id, CONTAINER_NAME)
        self.assertEqual(snapshot.host_id, "host-1")
        self.assertEqual(snapshot.observed_image_digest, IMAGE_DIGEST)
        self.assertIs(snapshot.evidence, self.evidence)

    def test_rejects_stopped_or_misnamed_container(self):
        self.api.container["State"]["Running"] = False
        with self.assertRaises(RuntimeInventoryError):
            self.collect()
        self.api.container["State"]["Running"] = True
        self.api.container["Name"] = "/yuki-" + "f" * 32
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_wrong_owner_task_worker_and_profile_labels(self):
        for key, value in (
            ("yuki.task_id", "other-task"),
            ("yuki.worker_id", "other-worker"),
            ("yuki.profile_id", "other-profile"),
            ("yuki.owner_sha256", "not-a-hash"),
        ):
            with self.subTest(key=key):
                original = self.api.container["Config"]["Labels"][key]
                self.api.container["Config"]["Labels"][key] = value
                with self.assertRaises(RuntimeInventoryError):
                    self.collect()
                self.api.container["Config"]["Labels"][key] = original

    def test_rejects_mutable_or_unpinned_image_digest(self):
        self.api.image["RepoDigests"] = ["python@" + "sha256:" + "f" * 64]
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_docker_configured_resource_mismatch(self):
        self.api.container["HostConfig"]["Memory"] -= 1
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_unbound_host_evidence(self):
        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            evidence_builder=lambda *_: RuntimeEnvironmentEvidence(
                task_id="other-task", spec_id=self.spec.spec_id,
                sandbox_image=self.profile.image,
                captured_at=self.evidence.captured_at, expires_at=self.evidence.expires_at,
                verifier_id="host-agent", runtime_versions={"python": "3.12.2"},
                installed_dependencies={}, cpu_millis=250, memory_mb=128, disk_free_mb=1024,
                workspace_root_id="root-1", approved_workspace_root_id="root-1",
                workspace_within_approved_root=True, workspace_symlink_free=True,
                workspace_writable=True,
                sandbox_policy_sha256=self.evidence.sandbox_policy_sha256,
                worker_id="worker-1",
            ),
        )
        with self.assertRaises(RuntimeInventoryError):
            collector("task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile)

    def test_requires_exact_lifecycle_container_name(self):
        with self.assertRaises(RuntimeInventoryError):
            self.collector("task-1", "worker-1", None, self.spec, self.profile)
        with self.assertRaises(RuntimeInventoryError):
            self.collector("task-1", "worker-1", "../other", self.spec, self.profile)


if __name__ == "__main__":
    unittest.main()
