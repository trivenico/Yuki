from types import MappingProxyType, SimpleNamespace

import pytest

from automation.gate_first_worker_deployment import validate_gate_first_supervisor
from automation.registered_specialist_worker import RegisteredSpecialistWorker
from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor
from core.gate_first_sandbox_adapter import GateFirstSandboxExecutorAdapter
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor
from core.sandbox_executor import SandboxExecutionProfile


def _supervisor(executor, profiles):
    worker = SimpleNamespace(sandbox_executor=executor, sandbox_profiles=profiles)
    registered = object.__new__(RegisteredSpecialistWorker)
    registered.worker = worker
    return SpecialistWorkerSupervisor((registered,))


def _valid_adapter():
    adapter = object.__new__(GateFirstSandboxExecutorAdapter)
    adapter.executor = object.__new__(GateFirstDockerSandboxExecutor)
    adapter.executor.expected_gate_host_uid = 1000
    adapter.executor.docker_binary = "/usr/bin/docker"
    adapter.executor.host_preflight = lambda: {
        "platform": "linux", "service_uid": 1000, "docker_binary": "/usr/bin/docker",
        "user_namespace_mapping_verified": True,
        "release_publisher_ownership_verified": True,
        "trusted_identity_callbacks": True,
        "trusted_supervisor_collector": True,
        "trusted_evidence_builder": True,
    }
    return adapter


def _valid_profiles():
    profile = object.__new__(SandboxExecutionProfile)
    profile.spec_id = "spec-1"
    return MappingProxyType({"spec-1": profile})


def test_gate_first_deployment_accepts_immutable_gate_first_workers():
    supervisor = _supervisor(_valid_adapter(), _valid_profiles())
    assert validate_gate_first_supervisor(supervisor) is supervisor


@pytest.mark.parametrize(
    "executor,profiles",
    [
        (None, _valid_profiles()),
        (object(), _valid_profiles()),
        (_valid_adapter(), {}),
        (_valid_adapter(), {"spec-1": object()}),
        (_valid_adapter(), {"spec-1": object.__new__(SandboxExecutionProfile)}),
    ],
)
def test_gate_first_deployment_fails_closed_for_invalid_workers(executor, profiles):
    with pytest.raises(RuntimeError):
        validate_gate_first_supervisor(_supervisor(executor, profiles))


def test_gate_first_deployment_rejects_mutable_profile_mapping():
    profile = object.__new__(SandboxExecutionProfile)
    profile.spec_id = "spec-1"
    with pytest.raises(RuntimeError):
        validate_gate_first_supervisor(_supervisor(_valid_adapter(), {"spec-1": profile}))


def test_gate_first_deployment_requires_real_supervisor():
    with pytest.raises(TypeError):
        validate_gate_first_supervisor(object())


def test_gate_first_deployment_rejects_missing_host_preflight():
    adapter = _valid_adapter()
    adapter.executor.host_preflight = None
    with pytest.raises(Exception, match="host preflight"):
        validate_gate_first_supervisor(_supervisor(adapter, _valid_profiles()))
