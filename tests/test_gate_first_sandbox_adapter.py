from __future__ import annotations

from types import SimpleNamespace

import pytest

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.gate_first_sandbox_adapter import GateFirstSandboxExecutorAdapter
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor
from core.sandbox_executor import SandboxExecutionProfile, SandboxExecutionResult
from core.sandbox_policy import SandboxPolicy


def _fixture():
    executor = object.__new__(GateFirstDockerSandboxExecutor)
    calls = []
    result = SandboxExecutionResult(
        exit_code=0, stdout=b"ok", stderr=b"", duration_seconds=0.1,
        timed_out=False, output_truncated=False, container_id="c" * 64,
    )
    def execute(**kwargs):
        calls.append(kwargs)
        if kwargs["lifecycle_callback"]:
            kwargs["lifecycle_callback"]("task.sandbox.started", "c" * 64, None)
        return result
    executor.execute = execute
    adapter = GateFirstSandboxExecutorAdapter(executor)
    spec = AgentExecutionSpec(
        spec_id="spec-1", role="worker", instructions="Run approved task",
        capabilities=("sandbox.execute",), output_types=("text",),
        budget=ExecutionBudget(max_runtime_seconds=30, max_memory_mb=512),
    )
    profile = SandboxExecutionProfile(
        spec_id="spec-1", image="python@sha256:" + "a" * 64,
        policy=SandboxPolicy().validate(), command=("python", "-V"),
        workspace_resolver=lambda task: "/workspace",
    )
    task = SimpleNamespace(
        run_id="task-1", owner_id="owner-1", state="running",
        lease_owner="worker-1", agent_execution_spec_id="spec-1",
    )
    return adapter, calls, spec, profile, task, result


def test_adapter_checks_authorization_before_gate_executor():
    adapter, calls, spec, profile, task, _ = _fixture()
    with pytest.raises(PermissionError):
        adapter.execute_authorized_task(
            task=task, execution_spec=spec, profile=profile,
            actor_id="service", worker_id="worker-1",
            authorize=lambda *_: False,
        )
    assert calls == []


def test_adapter_forwards_lease_cancellation_and_lifecycle_contract():
    adapter, calls, spec, profile, task, result = _fixture()
    events = []
    cancellation = lambda: False
    actual = adapter.execute_authorized_task(
        task=task, execution_spec=spec, profile=profile,
        actor_id="service", worker_id="worker-1",
        authorize=lambda actor, action, resource: (
            actor == "service" and action == "task:sandbox:execute" and resource == "task-1"
        ),
        cancellation_check=cancellation,
        lifecycle_callback=lambda *event: events.append(event),
    )
    assert actual is result
    assert calls[0]["task"] is task
    assert calls[0]["cancellation_check"] is cancellation
    assert calls[0]["lifecycle_callback"] is not None
    assert events[0][0] == "task.sandbox.started"


def test_adapter_rejects_mismatched_lease_before_authorization():
    adapter, calls, spec, profile, task, _ = _fixture()
    task.lease_owner = "other-worker"
    with pytest.raises(Exception, match="contract"):
        adapter.execute_authorized_task(
            task=task, execution_spec=spec, profile=profile,
            actor_id="service", worker_id="worker-1",
            authorize=lambda *_: True,
        )
    assert calls == []
