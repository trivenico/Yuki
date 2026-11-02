"""Application-facing conversation facade over durable agent sessions.

This layer records typed conversation events. It deliberately does not call
models, execute tools, resolve approvals, or resume workflows.
"""
from __future__ import annotations

from typing import Any

from automation.agent_sessions import AgentSession, AgentSessionStore, SessionEvent

_MESSAGE_KINDS = frozenset({"message.user", "message.assistant", "message.system"})
_TOOL_KINDS = frozenset({"tool.started", "tool.completed", "tool.failed"})
_MAX_MESSAGE_CHARS = 32_000
_MAX_SUMMARY_CHARS = 2_000


class ConversationService:
    """Small, authorization-preserving API for CLI and desktop conversation clients."""

    def __init__(self, sessions: AgentSessionStore):
        if not isinstance(sessions, AgentSessionStore):
            raise TypeError("sessions must be an AgentSessionStore")
        self.sessions = sessions

    def start(
        self, *, actor_id: str, idempotency_key: str, title: str,
        agent_id: str = "general", model_id: str | None = None,
        workspace_id: str | None = None,
    ) -> AgentSession:
        return self.sessions.create(
            actor_id=actor_id, idempotency_key=idempotency_key, title=title,
            agent_id=agent_id, model_id=model_id, workspace_id=workspace_id,
        )

    def add_message(
        self, *, actor_id: str, session_id: str, idempotency_key: str,
        role: str, content: str, expected_version: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionEvent:
        kind = f"message.{role}" if isinstance(role, str) else ""
        if kind not in _MESSAGE_KINDS:
            raise ValueError("role must be user, assistant, or system")
        if not isinstance(content, str) or not content.strip() or len(content) > _MAX_MESSAGE_CHARS:
            raise ValueError("content must be non-empty text within the message size limit")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        payload = {"content": content, "metadata": metadata}
        return self.sessions.append(
            actor_id=actor_id, session_id=session_id, idempotency_key=idempotency_key,
            kind=kind, payload=payload, expected_version=expected_version,
        )

    def record_tool_event(
        self, *, actor_id: str, session_id: str, idempotency_key: str,
        call_id: str, tool_name: str, status: str,
        summary: str, expected_version: int | None = None,
    ) -> SessionEvent:
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 200:
            raise ValueError("call_id must be non-empty bounded text")
        if not isinstance(tool_name, str) or not tool_name.strip() or len(tool_name) > 120:
            raise ValueError("tool_name must be non-empty bounded text")
        kind = f"tool.{status}" if isinstance(status, str) else ""
        if kind not in _TOOL_KINDS:
            raise ValueError("status must be started, completed, or failed")
        if not isinstance(summary, str) or len(summary) > _MAX_SUMMARY_CHARS:
            raise ValueError("summary must be bounded text")
        return self.sessions.append(
            actor_id=actor_id, session_id=session_id, idempotency_key=idempotency_key,
            kind=kind,
            payload={"call_id": call_id, "tool_name": tool_name, "summary": summary},
            expected_version=expected_version,
        )

    def history(
        self, *, actor_id: str, session_id: str, after_sequence: int = 0,
        limit: int = 100,
    ) -> list[SessionEvent]:
        return self.sessions.list_events(
            actor_id=actor_id, session_id=session_id,
            after_sequence=after_sequence, limit=limit,
        )

    def list_sessions(
        self, *, actor_id: str, before: tuple[str, str] | None = None,
        limit: int = 50,
    ) -> list[AgentSession]:
        return self.sessions.list_sessions(actor_id=actor_id, before=before, limit=limit)
