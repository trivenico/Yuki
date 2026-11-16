"""Compatibility bridge preserving desktop specialist workflows during chat migration.

Chat and memory consent are routed exclusively through the authenticated shared
conversation controller. Existing specialist, task, migration and backup
operations remain delegated to the existing authenticated desktop controller.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class SharedDesktopControllerBridge:
    """Drop-in desktop controller facade with shared-runtime chat authority."""

    _SPECIALIST_METHODS = (
        "list_specialists",
        "propose_agent_plan",
        "get_agent_plan",
        "dispatch_agent_plan",
        "cancel_agent_plan",
        "list_tasks",
        "get_task",
        "task_progress",
        "approve_task",
        "cancel_task",
        "preview_migration",
        "migrate_data",
        "create_backup",
        "restore_backup",
    )

    def __init__(self, chat: Any, specialist_controller: Any) -> None:
        for name in ("respond", "set_memory_context", "memory_context_enabled", "logout"):
            if not callable(getattr(chat, name, None)):
                raise TypeError("chat controller does not implement the shared chat contract.")
        for name in self._SPECIALIST_METHODS:
            if not callable(getattr(specialist_controller, name, None)):
                raise TypeError(f"specialist controller is missing {name}().")
        if not callable(getattr(specialist_controller, "logout", None)):
            raise TypeError("specialist controller must provide logout().")
        self._chat = chat
        self._specialists = specialist_controller
        self._closed = False

    def respond(self, message: str, **kwargs: Any) -> str:
        if self._closed:
            raise RuntimeError("desktop controller bridge is closed.")
        return self._chat.respond(message, **kwargs)

    def set_memory_context(self, enabled: bool) -> None:
        if self._closed:
            raise RuntimeError("desktop controller bridge is closed.")
        self._chat.set_memory_context(enabled)

    def memory_context_enabled(self) -> bool:
        if self._closed:
            raise RuntimeError("desktop controller bridge is closed.")
        return self._chat.memory_context_enabled()

    def _specialist_call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if self._closed:
            raise RuntimeError("desktop controller bridge is closed.")
        if name not in self._SPECIALIST_METHODS:
            raise AttributeError(name)
        return getattr(self._specialists, name)(*args, **kwargs)

    def list_specialists(self) -> Any:
        return self._specialist_call("list_specialists")

    def propose_agent_plan(self, goal: str) -> Any:
        return self._specialist_call("propose_agent_plan", goal)

    def get_agent_plan(self, plan_id: str) -> Any:
        return self._specialist_call("get_agent_plan", plan_id)

    def dispatch_agent_plan(self, plan_id: str) -> Any:
        return self._specialist_call("dispatch_agent_plan", plan_id)

    def cancel_agent_plan(self, plan_id: str) -> Any:
        return self._specialist_call("cancel_agent_plan", plan_id)

    def list_tasks(self, *, state: str | None = None, limit: int = 50) -> Any:
        return self._specialist_call("list_tasks", state=state, limit=limit)

    def get_task(self, task_id: str) -> Any:
        return self._specialist_call("get_task", task_id)

    def task_progress(
        self, task_id: str, *, after_sequence: int = 0, limit: int = 50,
    ) -> Any:
        return self._specialist_call(
            "task_progress", task_id, after_sequence=after_sequence, limit=limit,
        )

    def approve_task(self, task_id: str, *, expected_version: int) -> Any:
        return self._specialist_call(
            "approve_task", task_id, expected_version=expected_version,
        )

    def cancel_task(self, task_id: str, *, expected_version: int) -> Any:
        return self._specialist_call(
            "cancel_task", task_id, expected_version=expected_version,
        )

    def preview_migration(self, source: Path, destination: Path) -> Any:
        return self._specialist_call("preview_migration", source, destination)

    def migrate_data(self, source: Path, destination: Path) -> Any:
        return self._specialist_call("migrate_data", source, destination)

    def create_backup(self, destination: Path, passphrase: str) -> Any:
        return self._specialist_call("create_backup", destination, passphrase)

    def restore_backup(self, backup: Path, destination: Path, passphrase: str) -> Any:
        return self._specialist_call("restore_backup", backup, destination, passphrase)

    def logout(self) -> bool:
        """Revoke the old authenticated controller, then close shared chat state."""
        if self._closed:
            return False
        self._closed = True
        errors: list[Exception] = []
        revoked = False
        try:
            revoked = bool(self._specialists.logout())
        except Exception as exc:
            errors.append(exc)
        try:
            self._chat.logout()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError(
                "Desktop logout could not complete every controller cleanup step."
            ) from errors[0]
        return revoked

    close = logout
