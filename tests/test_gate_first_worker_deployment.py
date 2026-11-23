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
