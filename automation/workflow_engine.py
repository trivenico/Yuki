"""Bounded declarative workflow runner with explicit approval and result limits.

This is a synchronous in-process runner, not a durable scheduler or job queue.
It never evaluates code, invokes a shell, or performs implicit network access.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    action: str
    argument: str = ""
    requires_approval: bool = True


class WorkflowEngine:
    ALLOWED_ACTIONS = frozenset({"note", "wait_for_approval", "emit"})
    MAX_STEPS = 20
    MAX_NAME_CHARS = 100
    MAX_ARGUMENT_CHARS = 2_000
    MAX_OUTPUT_CHARS = 1_000

    def __init__(self, handlers: dict[str, Callable[[str], str]] | None = None, approver=None):
        if handlers is not None and not isinstance(handlers, dict):
            raise TypeError("handlers must be a mapping.")
        if handlers and any(key not in {"note", "emit"} for key in handlers):
            raise ValueError("Handlers may only override the allow-listed note and emit actions.")
        if handlers and any(not callable(handler) for handler in handlers.values()):
            raise TypeError("Every workflow handler must be callable.")
        if approver is not None and not callable(approver):
            raise TypeError("approver must be callable.")
        self.handlers = dict(handlers or {})
        self.approver = approver

    @staticmethod
    def _validate_step(index: int, step: Any) -> tuple[str, str, str, bool]:
        if isinstance(step, dict):
            allowed_keys = {"name", "action", "argument", "requires_approval"}
            if set(step) - allowed_keys:
                raise ValueError("Workflow step contains unsupported fields.")
            name = step.get("name", f"step-{index + 1}")
            action = step.get("action")
            argument = step.get("argument", "")
            approval = step.get("requires_approval", True)
        elif isinstance(step, WorkflowStep):
            name, action, argument, approval = (
                step.name, step.action, step.argument, step.requires_approval
            )
        else:
            raise ValueError("Each workflow step must be a mapping or WorkflowStep.")

        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > WorkflowEngine.MAX_NAME_CHARS
            or "\x00" in name
        ):
            raise ValueError("Step name must be non-empty text of at most 100 characters.")
        if not isinstance(action, str) or action not in WorkflowEngine.ALLOWED_ACTIONS:
            raise ValueError(f"Action {action!r} is not allow-listed.")
        if (
            not isinstance(argument, str)
            or len(argument) > WorkflowEngine.MAX_ARGUMENT_CHARS
            or "\x00" in argument
        ):
            raise ValueError("Step argument must be NUL-free text of at most 2000 characters.")
        if not isinstance(approval, bool):
            raise ValueError("requires_approval must be a boolean.")
        if action == "wait_for_approval" and not approval:
            raise ValueError("wait_for_approval always requires explicit approval.")
        return name.strip(), action, argument, approval

    def run(self, steps: list[WorkflowStep | dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(steps, (list, tuple)) or not 1 <= len(steps) <= self.MAX_STEPS:
            raise ValueError("Workflow must contain 1-20 steps.")

        validated = [self._validate_step(index, step) for index, step in enumerate(steps)]
        names = [step[0] for step in validated]
        if len(set(names)) != len(names):
            raise ValueError("Workflow step names must be unique.")

        results: list[dict[str, Any]] = []
        for name, action, argument, requires_approval in validated:
            needs_approval = requires_approval or action == "wait_for_approval"
            if needs_approval and (
                self.approver is None or self.approver(name, action, argument) is not True
            ):
                results.append({"step": name, "ok": False, "status": "approval_denied"})
                break

            if action == "wait_for_approval":
                results.append({"step": name, "ok": True, "status": "approved"})
                continue

            handler = self.handlers.get(action)
            if handler is None:
                results.append({
                    "step": name,
                    "ok": True,
                    "status": "recorded",
                    "output": argument[: self.MAX_OUTPUT_CHARS],
                })
                continue

            try:
                output = handler(argument)
                if not isinstance(output, str):
                    raise TypeError("Workflow handlers must return text.")
                results.append({
                    "step": name,
                    "ok": True,
                    "status": "completed",
                    "output": output[: self.MAX_OUTPUT_CHARS],
                })
            except Exception:
                results.append({"step": name, "ok": False, "status": "handler_failed"})
                break

        return results
