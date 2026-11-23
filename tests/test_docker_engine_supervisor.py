import unittest
import json
import os
import socket
import tempfile
import threading
import time
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
        self.image = {"Id": IMAGE_ID, "RepoDigests": ["python@" + IMAGE_DIGEST]}

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
            owner_hash_resolver=lambda _task: "e" * 64,
            evidence_builder=lambda *_: self.evidence,
        )

    def collect(self):
        return self.collector(
            "task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile,
        )

    def test_docker_api_client_rejects_every_non_inspect_endpoint(self):
        # Validation happens before a socket connection, so this test can
        # exercise the allowlist without access to a Docker daemon.
        api = DockerEngineAPI.__new__(DockerEngineAPI)
        api.socket_path = "/run/docker.sock"
        api.timeout_seconds = 1.0
        api.api_version = "v1.41"
        for path in (
            "/containers/json",
            "/containers/" + CONTAINER_NAME + "/start",
            "/containers/" + CONTAINER_NAME + "/exec",
            "/images/json",
            "/images/" + IMAGE_ID + "/delete",
            "/containers/" + CONTAINER_NAME + "/json?all=1",
            "/containers/../images/json",
            "//example.invalid/containers/json",
        ):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    api.get_json(path)

    def test_docker_api_client_allows_only_exact_inspect_path_shapes(self):
        api = DockerEngineAPI.__new__(DockerEngineAPI)
        api.socket_path = "/run/docker.sock"
        api.timeout_seconds = 1.0
        api.api_version = "v1.41"
        # A valid path passes local validation and proceeds to the socket;
        # connection failure is expected in this daemon-free unit test.
        with self.assertRaises(Exception) as raised:
            api.get_json("/containers/" + CONTAINER_NAME + "/json")
        self.assertNotIsInstance(raised.exception, ValueError)
        with self.assertRaises(Exception) as raised:
            api.get_json("/images/" + IMAGE_ID.replace(":", "%3A") + "/json")
        self.assertNotIsInstance(raised.exception, ValueError)

    def test_binds_live_container_labels_digest_and_policy(self):
        snapshot = self.collect()
        self.assertEqual(snapshot.container_id, "d" * 64)
        self.assertEqual(snapshot.container_name, CONTAINER_NAME)
        self.assertEqual(snapshot.host_id, "host-1")
        self.assertEqual(snapshot.observed_image_digest, IMAGE_DIGEST)
        self.assertIs(snapshot.evidence, self.evidence)

    def test_publishes_release_record_after_identity_checks_before_host_evidence(self):
        order = []
        self.api.container["State"]["Pid"] = 4321
        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            owner_hash_resolver=lambda _task: "e" * 64,
            release_record_publisher=lambda *args: order.append(("publish", args)),
            gate_identity_binder=lambda *_args: order.append(("bind",)),
            gate_releaser=lambda *args: order.append(("release", args)),
            evidence_builder=lambda *args: (order.append(("evidence", args)) or self.evidence),
        )
        snapshot = collector("task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile)
        self.assertEqual(order[0], (
            "publish", ("task-1", "worker-1", "d" * 64, self.profile.image, self.spec.spec_id)
        ))
        self.assertEqual(order[1][0], "evidence")
        self.assertEqual(order[2], ("bind",))
        self.assertEqual(order[3][0], "release")
        self.assertEqual(order[3][1][:6], ("task-1", "worker-1", "d" * 64, self.spec.spec_id, self.profile.image, self.profile.command))
        self.assertIs(order[3][1][6], self.evidence)
        self.assertEqual(snapshot.container_id, "d" * 64)

    def test_binds_inspected_gate_pid_after_evidence_before_authorization_release(self):
        order = []
        self.api.container["State"]["Pid"] = 4321
        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            owner_hash_resolver=lambda _task: "e" * 64,
            release_record_publisher=lambda *_args: order.append("publish"),
            evidence_builder=lambda *_args: (order.append("evidence") or self.evidence),
            gate_identity_binder=lambda container_id, pid, inspected: order.append(
                ("bind", container_id, pid, inspected["Id"])
            ),
            gate_releaser=lambda *_args: order.append("release"),
        )
        collector("task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile)
        self.assertEqual(order, [
            "publish", "evidence", ("bind", "d" * 64, 4321, "d" * 64), "release",
        ])

    def test_invalid_inspected_gate_pid_blocks_authorization_release(self):
        released = []
        self.api.container["State"]["Pid"] = 0
        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            owner_hash_resolver=lambda _task: "e" * 64,
            release_record_publisher=lambda *_args: None,
            evidence_builder=lambda *_args: self.evidence,
            gate_identity_binder=lambda *_args: self.fail("invalid PID must not be bound"),
            gate_releaser=lambda *_args: released.append(True),
        )
        with self.assertRaisesRegex(RuntimeInventoryError, "valid host PID"):
            collector("task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile)
        self.assertEqual(released, [])

    def test_invalid_host_identity_blocks_gate_release(self):
        released = []
        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "../invalid",
            owner_hash_resolver=lambda _task: "e" * 64,
            release_record_publisher=lambda *_args: None,
            gate_identity_binder=lambda *_args: None,
            gate_releaser=lambda *args: released.append(args),
            evidence_builder=lambda *_args: self.evidence,
        )
        with self.assertRaisesRegex(RuntimeInventoryError, "host identity"):
            collector("task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile)
        self.assertEqual(released, [])

    def test_gate_release_failure_prevents_successful_snapshot(self):
        self.api.container["State"]["Pid"] = 4321
        def fail_release(*_args):
            raise RuntimeInventoryError("gate channel unavailable")
        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            owner_hash_resolver=lambda _task: "e" * 64,
            release_record_publisher=lambda *_args: None,
            gate_identity_binder=lambda *_args: None,
            gate_releaser=fail_release,
            evidence_builder=lambda *_args: self.evidence,
        )
        with self.assertRaisesRegex(RuntimeInventoryError, "gate channel unavailable"):
            collector("task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile)

    def test_release_record_publisher_failure_blocks_host_evidence(self):
        measured = []
        def fail_publish(*_args):
            raise RuntimeInventoryError("release record publication failed")

        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            owner_hash_resolver=lambda _task: "e" * 64,
            release_record_publisher=fail_publish,
            gate_identity_binder=lambda *_args: None,
            gate_releaser=lambda *_args: self.fail("gate must not be released"),
            evidence_builder=lambda *_: measured.append(True),
        )
        with self.assertRaisesRegex(RuntimeInventoryError, "publication failed"):
            collector("task-1", "worker-1", CONTAINER_NAME, self.spec, self.profile)
        self.assertEqual(measured, [])

    def test_gate_releaser_requires_host_observed_identity_binder(self):
        with self.assertRaisesRegex(ValueError, "identity binder"):
            DockerEngineSupervisorCollector(
                api=self.api, host_id_provider=lambda: "host-1",
                owner_hash_resolver=lambda _task: "e" * 64,
                evidence_builder=lambda *_args: self.evidence,
                gate_releaser=lambda *_args: None,
            )

    def test_legacy_supervisor_without_release_publisher_remains_compatible(self):
        snapshot = self.collect()
        self.assertEqual(snapshot.container_id, "d" * 64)

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

    def test_rejects_same_digest_under_different_repository_reference(self):
        self.api.image["RepoDigests"] = ["other-python@" + IMAGE_DIGEST]
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_docker_configured_resource_mismatch(self):
        self.api.container["HostConfig"]["Memory"] -= 1
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_unbound_host_evidence(self):
        collector = DockerEngineSupervisorCollector(
            api=self.api, host_id_provider=lambda: "host-1",
            owner_hash_resolver=lambda _task: "e" * 64,
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


    def test_rejects_container_image_id_that_differs_from_inspected_image_object(self):
        self.api.image["Id"] = "sha256:" + "f" * 64
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_missing_or_oversized_repository_digest_metadata(self):
        self.api.image["RepoDigests"] = []
        with self.assertRaises(RuntimeInventoryError):
            self.collect()
        self.api.image["RepoDigests"] = ["python@" + IMAGE_DIGEST] * 257
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_malformed_repository_digest_entries(self):
        self.api.image["RepoDigests"] = ["python@" + IMAGE_DIGEST, None]
        with self.assertRaises(RuntimeInventoryError):
            self.collect()


class DockerEngineAPIUnixHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.socket_path = os.path.join(self.directory.name, "docker.sock")

    def tearDown(self):
        self.directory.cleanup()

    def request(self, *, status=200, body=b'{"Id":"container"}', delay_close=False, timeout=0.5):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        listener.listen(1)

        def serve():
            connection, _ = listener.accept()
            try:
                connection.settimeout(1)
                request_bytes = bytearray()
                while b"\\r\\n\\r\\n" not in request_bytes:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    request_bytes.extend(chunk)
                self.request_line = bytes(request_bytes).split(b"\\r\\n", 1)[0]
                if delay_close:
                    time.sleep(0.3)
                else:
                    reason = "OK" if status == 200 else "ERROR"
                    headers = (
                        f"HTTP/1.1 {status} {reason}\\r\\n"
                        f"Content-Type: application/json\\r\\n"
                        f"Content-Length: {len(body)}\\r\\n"
                        "Connection: close\\r\\n\\r\\n"
                    ).encode("ascii")
                    connection.sendall(headers + body)
            finally:
                connection.close()
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        api = DockerEngineAPI(socket_path=self.socket_path, timeout_seconds=timeout)
        return api, thread

    def test_unix_http_client_sends_get_and_decodes_inspect_json(self):
        api, thread = self.request()
        result = api.get_json("/containers/" + CONTAINER_NAME + "/json")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, {"Id": "container"})
        self.assertEqual(self.request_line, b"GET /v1.41/containers/" + CONTAINER_NAME.encode() + b"/json HTTP/1.1")

    def test_unix_http_client_rejects_non_200_status(self):
        api, thread = self.request(status=500, body=b"daemon error")
        with self.assertRaises(RuntimeInventoryError):
            api.get_json("/containers/" + CONTAINER_NAME + "/json")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_unix_http_client_rejects_invalid_json(self):
        api, thread = self.request(body=b"{invalid")
        with self.assertRaises(RuntimeInventoryError):
            api.get_json("/containers/" + CONTAINER_NAME + "/json")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_unix_http_client_rejects_oversized_response(self):
        api, thread = self.request(body=b" " * (1_048_576 + 1))
        with self.assertRaises(RuntimeInventoryError):
            api.get_json("/containers/" + CONTAINER_NAME + "/json")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_unix_http_client_enforces_socket_timeout(self):
        api, thread = self.request(delay_close=True, timeout=0.1)
        with self.assertRaises(RuntimeInventoryError):
            api.get_json("/containers/" + CONTAINER_NAME + "/json")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())



if __name__ == "__main__":
    unittest.main()
