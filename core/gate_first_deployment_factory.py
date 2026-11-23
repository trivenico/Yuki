"""Concrete, fail-closed composition for the gate-first Docker execution path.

The operator supplies the trust roots (identity, host evidence, release
publisher and host-specific verifiers). This module wires them to the existing
read-only Docker inspector and gate executor; it creates no worker, listener,
container, or background thread during import or factory construction.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.docker_engine_supervisor import DockerEngineAPI, DockerEngineSupervisorCollector
from core.execution_gate_docker_lifecycle import DockerGateContainerClient
from core.gate_first_host_preflight import validate_gate_first_host_environment
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence
from core.sandbox_executor import SandboxExecutionResult


def build_gate_first_sandbox_executor(
    *,
    docker_binary: str,
    expected_docker_sha256: str,
    docker_socket_path: str,
    docker_api_version: str,
    apparmor_profile: str,
    private_directory_root: str,
    expected_gate_host_uid: int,
    uid_resolver: Callable[[int], int],
    exec_observer: Callable[..., bool],
    host_id_provider: Callable[[], str],
    owner_hash_resolver: Callable[[str], str],
    evidence_builder: Callable[..., RuntimeEnvironmentEvidence],
    release_record_publisher: Callable[[str, str, str, str, str], object],
    user_namespace_mapping_verifier: Callable[[], object],
    release_publisher_ownership_verifier: Callable[[], object],
    trusted_observer_configuration_verifier: Callable[[], object],
    host_procfs_visibility_verifier: Callable[[], object],
    host_capabilities_verifier: Callable[[], object],
    post_exec_monitor: Callable[..., SandboxExecutionResult] | None = None,
) -> GateFirstDockerSandboxExecutor:
    """Wire trusted operator dependencies into one gate-first sandbox executor.

    The caller remains responsible for constructing workers with this executor
    and immutable per-spec profiles. The returned executor's host_preflight is
    mandatory and is run by gate-first supervisor validation before startup.
    """
    if type(docker_binary) is not str or not os.path.isabs(docker_binary) or os.path.islink(docker_binary):
        raise ValueError("docker_binary must be an absolute non-symlink executable path")
    canonical_docker = os.path.realpath(docker_binary)
    if type(docker_socket_path) is not str or not os.path.isabs(docker_socket_path):
        raise ValueError("docker_socket_path must be an absolute Unix socket path")
    canonical_socket = os.path.realpath(docker_socket_path)
    if type(expected_gate_host_uid) is not int or expected_gate_host_uid <= 0:
        raise ValueError("expected_gate_host_uid must be a non-root service UID")
    required_callables = {
        "uid_resolver": uid_resolver,
        "exec_observer": exec_observer,
        "host_id_provider": host_id_provider,
        "owner_hash_resolver": owner_hash_resolver,
        "evidence_builder": evidence_builder,
        "release_record_publisher": release_record_publisher,
        "user_namespace_mapping_verifier": user_namespace_mapping_verifier,
        "release_publisher_ownership_verifier": release_publisher_ownership_verifier,
        "trusted_observer_configuration_verifier": trusted_observer_configuration_verifier,
        "host_procfs_visibility_verifier": host_procfs_visibility_verifier,
        "host_capabilities_verifier": host_capabilities_verifier,
    }
    invalid = [name for name, callback in required_callables.items() if not callable(callback)]
    if invalid:
        raise TypeError("gate-first operator composition requires trusted callables: " + ", ".join(invalid))

    api = DockerEngineAPI(socket_path=canonical_socket, api_version=docker_api_version)
    docker_host = "unix://" + canonical_socket
    docker_client = DockerGateContainerClient(
        docker_binary=canonical_docker,
        docker_host=docker_host,
    )

    def collector_factory(*, gate_identity_binder, gate_releaser):
        return DockerEngineSupervisorCollector(
            api=api,
            host_id_provider=host_id_provider,
            owner_hash_resolver=owner_hash_resolver,
            evidence_builder=evidence_builder,
            release_record_publisher=release_record_publisher,
            gate_identity_binder=gate_identity_binder,
            gate_releaser=gate_releaser,
        )

    def host_preflight():
        return validate_gate_first_host_environment(
            docker_binary=canonical_docker,
            expected_docker_sha256=expected_docker_sha256,
            docker_socket_path=canonical_socket,
            private_directory_root=private_directory_root,
            expected_host_uid=expected_gate_host_uid,
            uid_resolver=uid_resolver,
            exec_observer=exec_observer,
            supervisor_collector_factory=collector_factory,
            trusted_evidence_builder=evidence_builder,
            user_namespace_mapping_verifier=user_namespace_mapping_verifier,
            release_publisher_ownership_verifier=release_publisher_ownership_verifier,
            trusted_observer_configuration_verifier=trusted_observer_configuration_verifier,
            host_procfs_visibility_verifier=host_procfs_visibility_verifier,
            host_capabilities_verifier=host_capabilities_verifier,
        )

    executor = GateFirstDockerSandboxExecutor(
        docker_client=docker_client,
        docker_binary=canonical_docker,
        apparmor_profile=apparmor_profile,
        private_directory_root=private_directory_root,
        expected_gate_host_uid=expected_gate_host_uid,
        uid_resolver=uid_resolver,
        exec_observer=exec_observer,
        supervisor_collector_factory=collector_factory,
        host_preflight=host_preflight,
        post_exec_monitor=post_exec_monitor,
    )
    return executor
