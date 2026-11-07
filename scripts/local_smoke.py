"""Deterministic local specialist-runtime smoke test (no network or model required).

This validates coordinator -> durable task/budget -> worker -> provider adapter
wiring with a fake provider. It does not validate real model inference.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from automation.agent_coordinator import SubtaskSpec
from automation.local_worker_runtime import LOCAL_OPERATOR_ACTOR, build_local_worker_runtime


class _SmokeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("smoke provider received an empty prompt")
        self.calls += 1
        return "Yuki local specialist runtime smoke test succeeded."


def run_smoke(workspace: str | Path) -> dict:
    provider = _SmokeProvider()
    runtime = build_local_worker_runtime(workspace, provider)
    plan = runtime.coordinator.submit(
        actor_id=LOCAL_OPERATOR_ACTOR,
        idempotency_key="yuki-local-smoke-plan-v1",
        subtasks=[
            SubtaskSpec(
                "smoke-research",
                "Verify local specialist execution wiring",
                ("research",),
                requires_approval=False,
            )
        ],
    )
    dispatched = runtime.coordinator.advance(
        actor_id=LOCAL_OPERATOR_ACTOR,
        plan_id=plan.plan_id,
    )
    if not dispatched.subtasks or not dispatched.subtasks[0].task_id:
        raise RuntimeError("Coordinator did not dispatch the smoke task.")

    task = runtime.supervisor.worker.run_once()
    if task is None or task.state != "succeeded":
        state = "no_task" if task is None else task.state
        raise RuntimeError(f"Specialist smoke task did not succeed (state={state}).")
    if provider.calls != 1:
        raise RuntimeError(f"Expected one provider call, observed {provider.calls}.")

    return {
        "result": "passed",
        "mode": "fake-provider-wiring-only",
        "plan_id": plan.plan_id,
        "task_id": task.task_id,
        "task_state": task.state,
        "provider_calls": provider.calls,
        "budget_usage": runtime.budget_store.usage(
            dispatched.subtasks[0].budget_id
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        help="Persistent test workspace. If omitted, a temporary directory is used.",
    )
    args = parser.parse_args()
    if args.workspace:
        result = run_smoke(Path(args.workspace).expanduser())
    else:
        with tempfile.TemporaryDirectory(prefix="yuki-smoke-") as directory:
            result = run_smoke(directory)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
