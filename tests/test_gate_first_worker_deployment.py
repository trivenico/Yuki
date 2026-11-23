from types import SimpleNamespace

import pytest

from automation.gate_first_worker_deployment import validate_gate_first_supervisor
from automation.registered_specialist_worker import RegisteredSpecialistWorker
from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor
from core.gate_first_sandbox_adapter import GateFirstSandboxExecutorAdapter


def _supervisor(executor, profiles):
    worker = SimpleNamespace(sandbox_executor=executor, sandbox_profiles=profiles)
    registered = object.__new__(RegisteredSpecialistWorker)
    registered.worker = worker
    return SpecialistWorkerSupervisor((registered,))


def test_gate_first_deployment_accepts_only_gate_first_workers_with_profiles():
    adapter = object.__new__(GateFirstSandboxExecutorAdapter)
    profiles = {"spec-1": SimpleNamespace(spec_id="spec-1")}
    supervisor = _supervisor(adapter, profiles)
    assert validate_gate_first_supervisor(supervisor) is supervisor


@pytest.mark.parametrize(
    "executor,profiles",
    [
        (None, {"spec-1": SimpleNamespace(spec_id="spec-1")}),
        (object(), {"spec-1": SimpleNamespace(spec_id="spec-1")}),
        (object.__new__(GateFirstSandboxExecutorAdapter), {}),
        (object.__new__(GateFirstSandboxExecutorAdapter), {"spec-1": SimpleNamespace(spec_id="other")}),
    ],
)
def test_gate_first_deployment_fails_closed_for_mixed_or_unconfigured_workers(executor, profiles):
    with pytest.raises(RuntimeError):
        validate_gate_first_supervisor(_supervisor(executor, profiles))


def test_gate_first_deployment_requires_real_supervisor():
    with pytest.raises(TypeError):
        validate_gate_first_supervisor(object())
