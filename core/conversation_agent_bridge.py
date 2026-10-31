"""Bridge persistent conversations to guarded specialist-agent execution.

This is an explicit agent-task entry point, not an implicit tool router. The caller
must supply a trusted actor identity and workspace scope from its authenticated
application boundary. Model output remains subject to the existing coordinator,
approval and worker controls.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import uuid4

from core.agent_execution_service import AgentExecutionService
from core.conversation_runtime import ConversationRuntime


@dataclass(frozen=True)
class AgentTurnResult:
    ok: bool
    session_id: str
    request_id: str
    message: str
    status: int
    plan_id: str | None = None
    plan_status: str | None = None
    replayed: bool = False
    error_code: str | None = None


class ConversationAgentBridge:
    """Persist an explicit agent request and return its approval-gated plan."""

    def __init__(
        self,
        conversations: ConversationRuntime,
        agents: AgentExecutionService,
        *,
        max_goal_chars: int = 4_000,
    ) -> None:
        if not isinstance(conversations, ConversationRuntime):
            raise TypeError("conversations must be a ConversationRuntime")
        if not isinstance(agents, AgentExecutionService):
            raise TypeError("agents must be an AgentExecutionService")
        if type(max_goal_chars) is not int or not 1 <= max_goal_chars <= 4_000:
            raise ValueError("max_goal_chars must be between 1 and 4000")
        self.conversations = conversations
        self.agents = agents
        self.max_goal_chars = max_goal_chars

    @staticmethod
    def _render_plan(plan: Any) -> str:
        lines = [
            f"Yuki created agent plan {plan.plan_id}.",
            f"Plan status: {plan.status}.",
            f"Specialist tasks: {len(plan.subtasks)}.",
        ]
        for item in plan.subtasks:
            approval = "human approval required" if item.requires_approval else "approval not required"
            lines.append(
                f"- {item.subtask_id} → {item.agent_id}: {item.status}; {approval}. "
                f"{item.description[:240]}"
            )
        lines.append(
            "No specialist work is reported as completed until an approved worker "
            "executes it and the plan is advanced."
        )
        return "\n".join(lines)

    def submit_agent_turn(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        user_message: str,
        session_id: str | None = None,
        title: str | None = None,
        request_id: str | None = None,
    ) -> AgentTurnResult:
        """Create an approval-gated plan from one persisted, idempotent conversation turn.

        actor_id must be obtained from trusted authentication middleware, never from
        the user message or an untrusted request-body field.
        """
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id must be supplied by a trusted caller")
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must be non-empty text")
        if len(user_message) > self.max_goal_chars:
            raise ValueError(f"user_message exceeds {self.max_goal_chars} characters")
        request_id = request_id or uuid4().hex
        if not self.agents.can_submit_goal(actor_id):
            return AgentTurnResult(
                False, session_id or "", request_id,
                "This account is not authorized to submit agent tasks.",
                3, error_code="agent_plan_unauthorized",
            )
        if session_id is None:
            session = self.conversations.store.create_session(
                workspace_id,
                title=(title or user_message[:80] or "Agent task"),
            )
            session_id = session["id"]
        else:
            self.conversations.store.get_session(workspace_id, session_id)

        turn = self.conversations.store.begin_turn(
            workspace_id, session_id, request_id, user_message,
        )
        if not turn.get("created", False):
            if turn["status"] == "completed" and turn.get("assistant_message_id"):
                messages = self.conversations.store.list_messages(workspace_id, session_id)
                saved = next(
                    (item for item in messages if item["id"] == turn["assistant_message_id"]),
                    None,
                )
                if saved is not None:
                    match = re.match(r"Yuki created agent plan ([A-Za-z0-9_.-]+)\.", saved["content"])
                    plan_id = match.group(1) if match else None
                    plan_status = None
                    if plan_id is not None:
                        try:
                            plan = self.agents.get_plan(actor_id=actor_id, plan_id=plan_id)
                            plan_status = plan.status
                        except (KeyError, PermissionError, ValueError):
                            # Keep the completed turn replayable if current plan access changed.
                            pass
                    return AgentTurnResult(
                        True, session_id, request_id, saved["content"], 0,
                        plan_id=plan_id, plan_status=plan_status, replayed=True,
                    )
            code = "turn_already_running" if turn["status"] == "running" else "turn_already_failed"
            message = (
                "This agent request is already running; it was not submitted again."
                if turn["status"] == "running"
                else "This agent request previously failed; submit a new request ID to retry."
            )
            return AgentTurnResult(
                False, session_id, request_id, message, 5,
                replayed=True, error_code=code,
            )

        try:
            plan = self.agents.submit_goal(
                actor_id=actor_id,
                idempotency_key=request_id,
                goal=user_message,
            )
            response = self._render_plan(plan)
            self.conversations.store.complete_turn(workspace_id, request_id, response)
            return AgentTurnResult(
                True, session_id, request_id, response, 0,
                plan_id=plan.plan_id, plan_status=plan.status,
            )
        except Exception as exc:
            try:
                self.conversations.store.fail_turn(
                    workspace_id, request_id, error_code="agent_plan_failed",
                )
            except (KeyError, ValueError):
                pass
            return AgentTurnResult(
                False, session_id, request_id,
                f"Yuki could not create the agent plan ({type(exc).__name__}).",
                4, error_code="agent_plan_failed",
            )
