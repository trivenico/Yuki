"""Shared application service for Yuki's companion clients.

CLI and desktop frontends should call this service rather than implement their own
conversation/memory policy. This layer owns the conversation session and the
session-scoped consent facade; it does not execute tools or dispatch tasks.
"""
from __future__ import annotations

from typing import Any

from companion.conversation import ConversationError, ConversationReply, ConversationSession
from companion.memory_consent import CompanionMemory
from automation.agent_coordinator import AgentPlan, SpecialistAgentCoordinator
from automation.task_runtime import TaskRecord, TaskRuntime
from companion.task_updates import CompanionTaskUpdate, CompanionTaskUpdates


class CompanionApplicationService:
    """One companion session API shared by terminal and graphical clients."""

    def __init__(
        self, provider: Any, *, memory: CompanionMemory | None = None,
        session: ConversationSession | None = None,
        agent_coordinator: SpecialistAgentCoordinator | None = None,
        task_runtime: TaskRuntime | None = None,
    ) -> None:
        if memory is not None and not isinstance(memory, CompanionMemory):
            raise TypeError("memory must be a CompanionMemory instance.")
        if session is not None and not isinstance(session, ConversationSession):
            raise TypeError("session must be a ConversationSession instance.")
        if agent_coordinator is not None and not isinstance(agent_coordinator, SpecialistAgentCoordinator):
            raise TypeError("agent_coordinator must be a SpecialistAgentCoordinator instance.")
        if task_runtime is not None and not isinstance(task_runtime, TaskRuntime):
            raise TypeError("task_runtime must be a TaskRuntime instance.")
        if session is not None and session.provider is not provider:
            raise ValueError("session and application service must use the same provider.")
        self.memory = memory or CompanionMemory()
        self.session = session or ConversationSession(provider)
        self.agent_coordinator = agent_coordinator
        self.task_runtime = task_runtime
        self.task_updates = CompanionTaskUpdates(task_runtime) if task_runtime is not None else None

    def respond(self, message: str) -> ConversationReply:
        """Respond using only explicitly enabled, relevant saved-memory context."""
        try:
            context = self.memory.context_entries(query=message)
        except Exception as exc:
            raise ConversationError("Yuki could not safely read saved-memory context; no request was sent.") from exc
        return self.session.respond(message, memory_context=context)

    def propose_agent_task(
        self, *, actor_id: str, idempotency_key: str, goal: str,
    ) -> AgentPlan:
        """Create a model-assisted proposal without enqueueing or executing tasks."""
        if self.agent_coordinator is None:
            raise ConversationError("Yuki's supervised agent coordinator is not configured.")
        return self.agent_coordinator.submit_model_plan(
            provider=self.session.provider,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            goal=goal,
        )

    def dispatch_agent_plan(self, *, actor_id: str, plan_id: str) -> AgentPlan:
        """Queue ready subtasks after an explicit client dispatch action.

        Dispatch only creates durable workflow tasks. Subtask execution remains
        behind the workflow engine's own approval and worker authorization gates.
        """
        if self.agent_coordinator is None:
            raise ConversationError("Yuki's supervised agent coordinator is not configured.")
        return self.agent_coordinator.advance(actor_id=actor_id, plan_id=plan_id)

    def task_progress(
        self, *, actor_id: str, task_id: str, after_sequence: int = 0,
        limit: int = 50,
    ) -> list[CompanionTaskUpdate]:
        """Read privacy-filtered incremental updates for an authorized task."""
        if self.task_updates is None:
            raise ConversationError("Yuki's supervised task runtime is not configured.")
        try:
            return self.task_updates.updates(
                actor_id=actor_id, task_id=task_id,
                after_sequence=after_sequence, limit=limit,
            )
        except PermissionError:
            raise
        except (ValueError, KeyError):
            raise
        except Exception as exc:
            raise ConversationError("Yuki could not safely retrieve task progress.") from exc

    def cancel_task(
        self, *, actor_id: str, task_id: str, expected_version: int,
    ) -> TaskRecord:
        """Request version-checked cancellation through the authorized task runtime."""
        if self.task_runtime is None:
            raise ConversationError("Yuki's supervised task runtime is not configured.")
        try:
            return self.task_runtime.cancel(
                actor_id=actor_id, task_id=task_id,
                expected_version=expected_version,
            )
        except (PermissionError, ValueError, KeyError):
            raise
        except Exception as exc:
            raise ConversationError("Yuki could not safely cancel this task.") from exc

    def set_role(self, name: str) -> dict:
        return self.session.set_role(name)

    def current_role(self) -> dict:
        return self.session.current_role()

    def reset_conversation(self) -> None:
        self.session.reset()

    def memory_status(self) -> dict[str, bool]:
        return {
            "saving_enabled": self.memory.enabled,
            "context_enabled": self.memory.context_enabled,
        }
