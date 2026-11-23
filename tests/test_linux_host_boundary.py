import tempfile
import os
import socket
import threading
import unittest
from pathlib import Path

from core.host_runtime_inventory import RuntimeInventoryError
from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.linux_host_boundary import LinuxHostBoundaryObserver, LinuxHostRuntimeEvidenceBuilder
from core.sandbox_executor import SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


class LinuxHostBoundaryObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.approved = self.root / "approved"
        self.workspace = self.approved / "task-workspace"
        self.workspace.mkdir(parents=True)
        self.proc = self.root / "proc"
        self.pid_dir = self.proc / "4242"
        self.pid_dir.mkdir(parents=True)
        self.cgroups = self.root / "cgroup"
        self.leaf = self.cgroups / "yuki" / "task"
        self.leaf.mkdir(parents=True)
        (self.pid_dir / "cgroup").write_text("0::/yuki/task\n", encoding="ascii")
        (self.cgroups / "memory.max").write_text(str(1024 * 1024 * 1024), encoding="ascii")
        (self.cgroups / "cpu.max").write_text("200000 100000", encoding="ascii")
        (self.cgroups / "yuki" / "memory.max").write_text(str(512 * 1024 * 1024), encoding="ascii")
        (self.cgroups / "yuki" / "cpu.max").write_text("100000 100000", encoding="ascii")
        (self.leaf / "memory.max").write_text(str(256 * 1024 * 1024), encoding="ascii")
        (self.leaf / "cpu.max").write_text("50000 100000", encoding="ascii")
        self.observer = LinuxHostBoundaryObserver(
            approved_workspace_root=str(self.approved),
            proc_root=str(self.proc), cgroup_root=str(self.cgroups),
        )
        self.inspect = {
            "Id": "a" * 64,
            "State": {"Running": True, "Pid": 4242},
            "Mounts": [{
                "Type": "bind", "Source": str(self.workspace),
                "Destination": "/workspace", "RW": True,
            }],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_measures_tightest_ancestor_cgroup_limits_and_workspace(self):
        result = self.observer.observe(
            container_inspect=self.inspect, expected_container_id="a" * 64,
        )
        self.assertEqual(result.cpu_millis, 500)
        self.assertEqual(result.memory_mb, 256)
        self.assertEqual(result.workspace_target, "/workspace")
        self.assertTrue(result.workspace_within_approved_root)
        self.assertTrue(result.workspace_symlink_free)
        self.assertTrue(result.workspace_writable)
        self.assertEqual(result.workspace_root_id, result.approved_workspace_root_id)
        self.assertGreater(result.disk_free_mb, 0)

    def test_rejects_wrong_container_identity(self):
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="b" * 64,
            )

    def test_rejects_workspace_outside_approved_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.inspect["Mounts"][0]["Source"] = str(outside)
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )

    def test_rejects_workspace_symlink(self):
        link = self.approved / "linked"
        link.symlink_to(self.workspace, target_is_directory=True)
        self.inspect["Mounts"][0]["Source"] = str(link)
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )

    def test_rejects_read_only_workspace_mount(self):
        self.inspect["Mounts"][0]["RW"] = False
        result = self.observer.observe(
            container_inspect=self.inspect, expected_container_id="a" * 64,
        )
        self.assertFalse(result.workspace_writable)

    def test_rejects_missing_finite_cgroup_limits(self):
        (self.leaf / "memory.max").write_text("max", encoding="ascii")
        (self.cgroups / "yuki" / "memory.max").write_text("max", encoding="ascii")
        (self.cgroups / "memory.max").write_text("max", encoding="ascii")
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )


    def test_runtime_evidence_builder_uses_host_measurements_and_trusted_probe(self):
        spec = AgentExecutionSpec(
            spec_id="host-observer", role="reviewer", instructions="Inspect safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=100, max_steps=2,
            ),
        )
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id, image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250),
            command=("python", "--version"), workspace_resolver=lambda _: str(self.workspace),
        )
        builder = LinuxHostRuntimeEvidenceBuilder(
            observer=self.observer, verifier_id="host-agent",
            runtime_inventory_provider=lambda *_: {
                "runtime_versions": {"python": "3.12.2"},
                "installed_dependencies": {"requests": "2.32.0"},
            },
        )
        evidence = builder(
            "task-1", "worker-1", "a" * 64,
            self.inspect, spec, profile,
        )
        self.assertEqual(evidence.cpu_millis, 500)
        self.assertEqual(evidence.memory_mb, 256)
        self.assertEqual(evidence.runtime_versions["python"], "3.12.2")
        self.assertEqual(evidence.installed_dependencies["requests"], "2.32.0")
        self.assertEqual(evidence.worker_id, "worker-1")
        self.assertEqual(evidence.workspace_root_id, evidence.approved_workspace_root_id)

    @unittest.skipUnless(hasattr(socket, "SO_PEERCRED"), "Linux Unix peer credentials required")
    def test_linux_evidence_builder_integrates_signed_host_agent_and_durable_replay(self):
        from core.host_runtime_inventory_ipc import (
            HostRuntimeInventoryAgent,
            build_authenticated_runtime_inventory_provider,
        )
        from core.host_runtime_inventory_service import HostRuntimeInventoryUnixService

        spec = AgentExecutionSpec(
            spec_id="host-observer", role="reviewer", instructions="Inspect safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=100, max_steps=2,
            ),
        )
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id, image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250),
            command=("python", "--version"), workspace_resolver=lambda _: str(self.workspace),
        )
        key = b"yuki-end-to-end-host-agent-key-material-32-bytes"
        socket_path = str(self.root / "host-agent.sock")
        agent = HostRuntimeInventoryAgent(
            source_id="linux-host-agent", hmac_key=key,
            expected_client_uid=os.getuid(),
            inventory_collector=lambda task, worker, container, image, spec_id: {
                "runtime_versions": {"Python": "3.12.2"},
                "installed_dependencies": {"Requests": "2.32.0"},
            },
        )
        service = HostRuntimeInventoryUnixService(
            agent=agent, socket_path=socket_path,
            service_uid=os.getuid(), socket_gid=os.getgid(), socket_mode=0o600,
        )
        service.start()
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            provider = build_authenticated_runtime_inventory_provider(
                socket_path=socket_path, expected_agent_uid=os.getuid(),
                hmac_key=key, source_id="linux-host-agent",
                nonce_database_path=str(self.root / "runtime-nonces.sqlite3"),
            )
            builder = LinuxHostRuntimeEvidenceBuilder(
                observer=self.observer, verifier_id="authenticated-host-agent",
                runtime_inventory_provider=provider,
            )
            evidence = builder(
                "task-1", "worker-1", "a" * 64, self.inspect, spec, profile,
            )
            self.assertEqual(evidence.runtime_versions["python"], "3.12.2")
            self.assertEqual(evidence.installed_dependencies["requests"], "2.32.0")
            self.assertEqual(evidence.cpu_millis, 500)
            self.assertEqual(evidence.memory_mb, 256)
            self.assertEqual(evidence.worker_id, "worker-1")
        finally:
            service.close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_runtime_evidence_builder_rejects_effective_limit_below_policy(self):
        spec = AgentExecutionSpec(
            spec_id="host-observer", role="reviewer", instructions="Inspect safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=100, max_steps=2,
            ),
        )
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id, image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250),
            command=("python", "--version"), workspace_resolver=lambda _: str(self.workspace),
        )
        (self.leaf / "cpu.max").write_text("20000 100000", encoding="ascii")
        builder = LinuxHostRuntimeEvidenceBuilder(
            observer=self.observer, verifier_id="host-agent",
            runtime_inventory_provider=lambda *_: {
                "runtime_versions": {"python": "3.12.2"},
                "installed_dependencies": {},
            },
        )
        with self.assertRaises(RuntimeInventoryError):
            builder("task-1", "worker-1", "a" * 64, self.inspect, spec, profile)

    def test_rejects_non_v2_or_malformed_cgroup_path(self):
        (self.pid_dir / "cgroup").write_text("2:cpu:/legacy\n", encoding="ascii")
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )


    def test_docker_engine_collector_composes_existing_host_boundary_builder(self):
        from core.docker_engine_supervisor import DockerEngineAPI, DockerEngineSupervisorCollector

        spec = AgentExecutionSpec(
            spec_id="host-observer", role="reviewer", instructions="Inspect safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=100, max_steps=2,
            ),
        )
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id, image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250),
            command=("python", "--version"), workspace_resolver=lambda _: str(self.workspace),
        )
        container_name = "yuki-" + "c" * 32
        container_id = "a" * 64
        image_id = "sha256:" + "b" * 64
        owner_hash = "e" * 64
        self.inspect.update({
            "Name": "/" + container_name,
            "Image": image_id,
            "Config": {
                "Image": profile.image,
                "Labels": {
                    "yuki.managed_by": "yuki",
                    "yuki.task_id": "task-1",
                    "yuki.worker_id": "worker-1",
                    "yuki.profile_id": profile.spec_id,
                    "yuki.image_digest": profile.image.rsplit("@", 1)[-1],
                    "yuki.owner_sha256": owner_hash,
                },
            },
            "HostConfig": {
                "Memory": 128 * 1024 * 1024,
                "NanoCpus": 250_000_000,
            },
        })
        class FakeDockerAPI(DockerEngineAPI):
            def __init__(self, container, image):
                self.container = container
                self.image = image

            def get_json(self, path):
                if path == f"/containers/{container_name}/json":
                    return self.container
                if path == f"/images/{image_id}/json":
                    return self.image
                raise AssertionError(path)

        host_builder = LinuxHostRuntimeEvidenceBuilder(
            observer=self.observer, verifier_id="host-agent",
            runtime_inventory_provider=lambda *_: {
                "runtime_versions": {"python": "3.12.2"},
                "installed_dependencies": {"requests": "2.32.0"},
            },
        )
        collector = DockerEngineSupervisorCollector(
            api=FakeDockerAPI(self.inspect, {"RepoDigests": [profile.image]}),
            host_id_provider=lambda: "host-1",
            owner_hash_resolver=lambda _task: owner_hash,
            evidence_builder=host_builder,
        )
        snapshot = collector("task-1", "worker-1", container_name, spec, profile)
        self.assertEqual(snapshot.container_id, container_id)
        self.assertEqual(snapshot.container_name, container_name)
        self.assertEqual(snapshot.evidence.cpu_millis, 500)
        self.assertEqual(snapshot.evidence.memory_mb, 256)
        self.assertEqual(snapshot.evidence.worker_id, "worker-1")


if __name__ == "__main__":
    unittest.main()
