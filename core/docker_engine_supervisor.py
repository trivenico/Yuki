"""Docker Engine control-plane collection for supervisor-owned sandbox evidence.

This collector reads Docker over a local Unix socket. It verifies the live
container, Yuki ownership labels, immutable image digest and configured resource
limits. Runtime/package/workspace measurements remain the responsibility of an
operator-trusted host observer supplied as evidence_builder; Docker inspect JSON
is not treated as host attestation.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import stat
import time
from typing import Callable, Mapping
from urllib.parse import quote

from automation.agent_execution_spec import AgentExecutionSpec
from core.host_runtime_inventory import RuntimeInventoryError
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence
from core.sandbox_executor import DockerSandboxExecutor, SandboxExecutionProfile
from core.supervisor_runtime_inventory import SupervisorRuntimeSnapshot

_CONTAINER_NAME = re.compile(r"^yuki-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_OWNER_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_RESPONSE_BYTES = 1_048_576


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP/1.1 connection restricted to one configured Unix-domain socket."""

    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except Exception:
            sock.close()
            raise
        self.sock = sock


class DockerEngineAPI:
    """Small bounded Docker Engine API client; TCP endpoints and redirects are absent."""

    def __init__(self, *, socket_path: str, timeout_seconds: float = 3.0, api_version: str = "v1.41") -> None:
        if type(socket_path) is not str or not os.path.isabs(socket_path) or os.path.islink(socket_path):
            raise ValueError("Docker Engine socket must be an absolute non-symlink path.")
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 10:
            raise ValueError("timeout_seconds must be between 0.1 and 10.")
        if type(api_version) is not str or not re.fullmatch(r"v1\.[0-9]{2}", api_version):
            raise ValueError("Docker Engine API version must be explicitly pinned.")
        try:
            info = os.stat(socket_path, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("Docker Engine socket must exist.") from exc
        if not stat.S_ISSOCK(info.st_mode):
            raise ValueError("Docker Engine path must be a Unix socket.")
        if not hasattr(os, "getuid") or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("Docker Engine socket must be owned by this service user and not group/world writable.")
        self.socket_path = os.path.realpath(socket_path)
        self.timeout_seconds = float(timeout_seconds)
        self.api_version = api_version

    def get_json(self, path: str) -> Mapping[str, object]:
        if type(path) is not str or not path.startswith("/") or "?" in path or "#" in path or "\x00" in path:
            raise ValueError("Docker Engine API path is invalid.")
        connection = _UnixHTTPConnection(self.socket_path, self.timeout_seconds)
        try:
            connection.request("GET", f"/{self.api_version}{path}", headers={"Accept": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                response.read(4096)
                raise RuntimeInventoryError(f"Docker Engine API returned HTTP {response.status}.")
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeInventoryError("Docker Engine response exceeded the configured byte ceiling.")
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeInventoryError("Docker Engine returned invalid JSON.") from exc
            if not isinstance(decoded, dict):
                raise RuntimeInventoryError("Docker Engine response must be a JSON object.")
            return decoded
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            raise RuntimeInventoryError("Docker Engine control-plane request failed.") from exc
        finally:
            connection.close()


class DockerEngineSupervisorCollector:
    """Callable collector that binds trusted host evidence to a live Docker container.

    evidence_builder must be an operator-controlled host observer. It must measure
    runtime/package versions, workspace containment/symlink/write state, effective
    cgroup limits and disk availability from outside task-controlled code.
    """

    def __init__(
        self,
        *,
        api: DockerEngineAPI,
        host_id_provider: Callable[[], str],
        owner_hash_resolver: Callable[[str], str],
        evidence_builder: Callable[[str, str, str, Mapping[str, object], AgentExecutionSpec, SandboxExecutionProfile], RuntimeEnvironmentEvidence],
        container_lookup_attempts: int = 3,
        retry_delay_seconds: float = 0.1,
    ) -> None:
        if (not isinstance(api, DockerEngineAPI) or not callable(host_id_provider)
                or not callable(owner_hash_resolver) or not callable(evidence_builder)):
            raise TypeError("Docker API, host identity, owner binding and trusted evidence providers are required.")
        if type(container_lookup_attempts) is not int or not 1 <= container_lookup_attempts <= 5:
            raise ValueError("container_lookup_attempts must be between 1 and 5.")
        if type(retry_delay_seconds) not in (int, float) or isinstance(retry_delay_seconds, bool) or not 0 <= retry_delay_seconds <= 0.5:
            raise ValueError("retry_delay_seconds must be between 0 and 0.5.")
        self.container_lookup_attempts = container_lookup_attempts
        self.retry_delay_seconds = float(retry_delay_seconds)
        self.api = api
        self.host_id_provider = host_id_provider
        self.owner_hash_resolver = owner_hash_resolver
        self.evidence_builder = evidence_builder

    def __call__(
        self,
        task_id: str,
        worker_id: str,
        expected_container_id: str | None,
        spec: AgentExecutionSpec,
        profile: SandboxExecutionProfile,
    ) -> SupervisorRuntimeSnapshot:
        if type(expected_container_id) is not str or not _CONTAINER_NAME.fullmatch(expected_container_id):
            raise RuntimeInventoryError("an exact Yuki lifecycle container name is required.")
        container_path = "/containers/" + quote(expected_container_id, safe="") + "/json"
        container = None
        last_error = None
        for attempt in range(self.container_lookup_attempts):
            try:
                container = self.api.get_json(container_path)
                break
            except RuntimeInventoryError as exc:
                last_error = exc
                if attempt + 1 < self.container_lookup_attempts and self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds)
        if container is None:
            raise RuntimeInventoryError("Docker container did not become inspectable within the bounded startup window.") from last_error
        container_id = container.get("Id")
        name = container.get("Name")
        state = container.get("State")
        config = container.get("Config")
        host_config = container.get("HostConfig")
        if (
            type(container_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", container_id)
            or name != "/" + expected_container_id
            or not isinstance(state, dict) or state.get("Running") is not True
            or not isinstance(config, dict) or not isinstance(host_config, dict)
        ):
            raise RuntimeInventoryError("Docker container identity or running state is invalid.")
        labels = config.get("Labels")
        expected_labels = {
            "yuki.managed_by": "yuki",
            "yuki.task_id": task_id,
            "yuki.worker_id": worker_id,
            "yuki.profile_id": profile.spec_id,
            "yuki.image_digest": profile.image.rsplit("@", 1)[-1],
        }
        if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in expected_labels.items()):
            raise RuntimeInventoryError("Docker container ownership labels do not match the pinned task and worker.")
        expected_owner_hash = self.owner_hash_resolver(task_id)
        if (type(expected_owner_hash) is not str or not _OWNER_HASH.fullmatch(expected_owner_hash)
                or labels.get("yuki.owner_sha256") != expected_owner_hash):
            raise RuntimeInventoryError("Docker container owner binding does not match the trusted task owner.")

        image_id = container.get("Image")
        if type(image_id) is not str or not _SHA256.fullmatch(image_id):
            raise RuntimeInventoryError("Docker container image ID is invalid.")
        image_info = self.api.get_json("/images/" + quote(image_id, safe="") + "/json")
        repo_digests = image_info.get("RepoDigests")
        expected_digest = profile.image.rsplit("@", 1)[-1]
        if not _SHA256.fullmatch(expected_digest) or not isinstance(repo_digests, list):
            raise RuntimeInventoryError("Docker image digest metadata is unavailable.")
        if not any(type(item) is str and item.endswith("@" + expected_digest) for item in repo_digests):
            raise RuntimeInventoryError("Docker image RepoDigest does not match the pinned immutable image.")

        expected_memory = profile.policy.memory_mb * 1024 * 1024
        expected_nano_cpus = profile.policy.cpu_millis * 1_000_000
        if host_config.get("Memory") != expected_memory or host_config.get("NanoCpus") != expected_nano_cpus:
            raise RuntimeInventoryError("Docker configured memory or CPU limits do not match the sandbox policy.")

        evidence = self.evidence_builder(task_id, worker_id, expected_container_id, container, spec, profile)
        if not isinstance(evidence, RuntimeEnvironmentEvidence):
            raise RuntimeInventoryError("trusted host evidence builder returned an invalid evidence type.")
        policy_digest = DockerSandboxExecutor._policy_digest(profile.policy)
        if (
            evidence.task_id != task_id or evidence.worker_id != worker_id
            or evidence.spec_id != spec.spec_id or evidence.sandbox_image != profile.image
            or evidence.sandbox_policy_sha256 != policy_digest
        ):
            raise RuntimeInventoryError("trusted host evidence is not bound to this task, worker, image and policy.")
        host_id = self.host_id_provider()
        if type(host_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", host_id):
            raise RuntimeInventoryError("trusted host identity provider returned an invalid identifier.")
        return SupervisorRuntimeSnapshot(
            evidence=evidence,
            host_id=host_id,
            container_id=container_id,
            container_name=expected_container_id,
            worker_session_id=worker_id,
            observed_image_digest=expected_digest,
            observed_policy_sha256=policy_digest,
        )
