"""Composition root for authenticated native commands.

The caller must obtain actor_id from the live authenticated session, never from
message text or UI input. Memory resolution must return that actor's private
CompanionMemory facade. All dispatched commands are audited before execution.
"""
from __future__ import annotations

import uuid
from typing import Callable

from automation.task_runtime import TaskRuntime
from companion.memory_consent import CompanionMemory
from core.approval_audit import SQLiteApprovalAuditLog
from core.native_intent_dispatcher import NativeIntentDispatcher, NativeDispatchResult
from core.native_intents import IntentKind, NativeIntentParser
from core.native_memory_command_adapter import NativeMemoryCommandAdapter
from core.native_task_command_adapter import NativeTaskCommandAdapter


class NativeCommandRuntime:
    """Wire parser, guarded adapters and the durable audit log into one service."""

    def __init__(
        self,
        *,
        task_runtime: TaskRuntime,
        memory_for_actor: Callable[[str], CompanionMemory],
        audit_log: SQLiteApprovalAuditLog,
        authorize_capability: Callable[[str, str], bool],
        status_provider: Callable[[str], dict] | None = None,
    ) -> None:
        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("task_runtime must be a TaskRuntime.")
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("audit_log must be a SQLiteApprovalAuditLog.")
        if not callable(memory_for_actor) or not callable(authorize_capability):
            raise TypeError("memory and capability resolvers must be callable.")
        if status_provider is not None and not callable(status_provider):
            raise TypeError("status_provider must be callable.")
        self._audit_log = audit_log
        self._status_provider = status_provider
        self._tasks = NativeTaskCommandAdapter(
            task_runtime, authorize_capability=authorize_capability,
        )
        self._memory = NativeMemoryCommandAdapter(
            memory_for_actor, authorize_capability=authorize_capability,
        )
        handlers = {
            IntentKind.HELP: self._help,
            IntentKind.STATUS: self._status,
            **self._tasks.handlers,
            **self._memory.handlers,
        }
        self._dispatcher = NativeIntentDispatcher(
            NativeIntentParser(),
            handlers,
            authorize=self._authorize,
            audit=self._audit,
        )

    @property
    def supported_commands(self) -> tuple[str, ...]:
        return (
            "/help", "/status", "/task list", "/task show ID",
            "/memory search QUERY", "/memory remember FACT",
        )

    def dispatch(
        self,
        *,
        authenticated_actor_id: str,
        text: str,
        confirmed: bool = False,
    ) -> NativeDispatchResult:
        """Dispatch using identity obtained from the authenticated app session."""
        if (
            not isinstance(authenticated_actor_id, str)
            or not authenticated_actor_id.strip()
            or len(authenticated_actor_id) > 200
            or any(ord(char) < 32 or ord(char) == 127 for char in authenticated_actor_id)
        ):
            raise ValueError("authenticated_actor_id must be trusted, non-empty bounded text.")
        return self._dispatcher.dispatch(
            authenticated_actor_id,
            text,
            confirmed=confirmed,
        )

    def _authorize(self, actor_id: str, kind: IntentKind) -> bool:
        if kind in {IntentKind.HELP, IntentKind.STATUS}:
            return True
        if kind in self._tasks.handlers:
            return self._tasks.authorize_intent(actor_id, kind)
        if kind in self._memory.handlers:
            return self._memory.authorize_intent(actor_id, kind)
        return False

    def _audit(self, event: dict) -> None:
        # Do not persist command text, arguments, or memory contents.
        if not isinstance(event, dict) or event.get("event") != "native_intent.dispatch":
            raise ValueError("unsupported native command audit event.")
        intent = event.get("intent")
        if not isinstance(intent, str) or len(intent) > 64:
            raise ValueError("invalid native intent audit identity.")
        argument_count = event.get("argument_count")
        if type(argument_count) is not int or not 0 <= argument_count <= 16:
            raise ValueError("invalid native intent argument count.")
        actor_id = event.get("actor_id")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("invalid native intent actor.")
        confirmed = event.get("confirmed")
        if type(confirmed) is not bool:
            raise ValueError("invalid native intent confirmation flag.")
        self._audit_log.append(
            event_type="native_intent_dispatched",
            pending_id=uuid.uuid4().hex,
            actor=actor_id,
            skill_id=f"native:{intent}"[:120],
        )

    @staticmethod
    def _help(actor_id: str, arguments: tuple[str, ...]) -> str:
        if arguments:
            raise ValueError("help does not accept arguments.")
        return (
            "Available commands: /status, /task list, /task show ID, "
            "/memory search QUERY, /memory remember FACT. "
            "Memory writes require memory consent and explicit confirmation."
        )

    def _status(self, actor_id: str, arguments: tuple[str, ...]) -> str:
        if arguments:
            raise ValueError("status does not accept arguments.")
        if self._status_provider is None:
            return "Yuki native command service is available. Task and memory services are connected."
        status = self._status_provider(actor_id)
        if not isinstance(status, dict):
            raise TypeError("status provider must return a dictionary.")
        # Keep status output compact and JSON serializable; never expose credentials.
        import json
        return json.dumps(status, ensure_ascii=False, separators=(",", ":"), default=str)[:4000]


__all__ = ["NativeCommandRuntime"]
