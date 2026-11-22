"""Compatibility bridge preserving desktop specialist workflows during chat migration.

Chat and memory consent are routed exclusively through the authenticated shared
conversation controller. Existing specialist, task, migration and backup
operations remain delegated to the existing authenticated desktop controller.
"""
from __future__ import annotations

from pathlib import Path
from threading import Condition, RLock
from typing import Any

from ui.desktop.native_command_session import AuthenticatedNativeSessionExpired


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

    def __init__(self, chat: Any, specialist_controller: Any, native_commands: Any = None) -> None:
        for name in ("respond", "list_conversations", "select_conversation", "history", "set_memory_context", "memory_context_enabled", "logout"):
            if not callable(getattr(chat, name, None)):
                raise TypeError("chat controller does not implement the shared chat contract.")
        for name in self._SPECIALIST_METHODS:
            if not callable(getattr(specialist_controller, name, None)):
                raise TypeError(f"specialist controller is missing {name}().")
        if not callable(getattr(specialist_controller, "logout", None)):
            raise TypeError("specialist controller must provide logout().")
        if native_commands is not None and not callable(getattr(native_commands, "dispatch", None)):
            raise TypeError("native_commands must provide dispatch().")
        self._chat = chat
        self._specialists = specialist_controller
        self._native_commands = native_commands
        self._closed = False
        self._logout_complete = False
        self._logout_in_progress = False
        self._specialist_logout_complete = False
        self._chat_logout_complete = False
        self._active_operations = 0
        self._logout_condition = Condition(RLock())

    def _chat_call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Serialize every authenticated chat operation against session teardown."""
        with self._logout_condition:
            if self._closed:
                raise RuntimeError("desktop controller bridge is closed.")
            self._active_operations += 1
        authorization_error: PermissionError | None = None
        try:
            return getattr(self._chat, name)(*args, **kwargs)
        except PermissionError as exc:
            authorization_error = exc
        finally:
            with self._logout_condition:
                self._active_operations -= 1
                self._logout_condition.notify_all()
        # Leave the active set before cleanup; logout waits for active operations.
        if authorization_error is not None:
            self._close_after_authorization_failure()
            raise authorization_error

    def respond(self, message: str, **kwargs: Any) -> str:
        return self._chat_call("respond", message, **kwargs)

    def list_conversations(self, *, limit: int = 50) -> Any:
        return self._chat_call("list_conversations", limit=limit)

    def select_conversation(self, conversation_id: str) -> Any:
        return self._chat_call("select_conversation", conversation_id)

    def history(self, *, limit: int = 100) -> Any:
        return self._chat_call("history", limit=limit)

    def set_memory_context(self, enabled: bool) -> None:
        self._chat_call("set_memory_context", enabled)

    def memory_context_enabled(self) -> bool:
        return self._chat_call("memory_context_enabled")

    def set_memory_saving_enabled(self, enabled: bool) -> None:
        method = getattr(self._chat, "set_memory_saving_enabled", None)
        if not callable(method):
            raise RuntimeError("Memory-saving consent is not supported by this desktop session.")
        self._chat_call("set_memory_saving_enabled", enabled)

    def memory_saving_enabled(self) -> bool:
        method = getattr(self._chat, "memory_saving_enabled", None)
        if not callable(method):
            raise RuntimeError("Memory-saving consent is not supported by this desktop session.")
        return bool(self._chat_call("memory_saving_enabled"))

    def dispatch_native_command(self, text: str, *, confirmed: bool = False) -> Any:
        """Run an explicit native command through the live authenticated session."""
        with self._logout_condition:
            if self._closed:
                raise RuntimeError("desktop controller bridge is closed.")
            if self._native_commands is None:
                raise RuntimeError("Native command runtime is not configured for this desktop session.")
            # Treat native commands as in-flight session operations. Logout
            # closes admission first, then waits for every admitted operation
            # to leave before invalidating memory and revoking credentials.
            self._active_operations += 1
        authorization_error: AuthenticatedNativeSessionExpired | None = None
        try:
            return self._native_commands.dispatch(text, confirmed=confirmed)
        except AuthenticatedNativeSessionExpired as exc:
            authorization_error = exc
        finally:
            with self._logout_condition:
                self._active_operations -= 1
                self._logout_condition.notify_all()
        self._close_after_authorization_failure()
        raise authorization_error

    def _specialist_call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self._SPECIALIST_METHODS:
            raise AttributeError(name)
        with self._logout_condition:
            if self._closed:
                raise RuntimeError("desktop controller bridge is closed.")
            self._active_operations += 1
        authorization_error: PermissionError | None = None
        try:
            return getattr(self._specialists, name)(*args, **kwargs)
        except PermissionError as exc:
            authorization_error = exc
        finally:
            with self._logout_condition:
                self._active_operations -= 1
                self._logout_condition.notify_all()
        # Do not invoke logout until this operation has left the active set;
        # otherwise logout would wait for the very call that is invoking it.
        if authorization_error is not None:
            self._close_after_authorization_failure()
            raise authorization_error

    def _close_after_authorization_failure(self) -> None:
        """Fail closed when either authenticated surface reports revoked access."""
        try:
            self.logout()
        except Exception as exc:
            raise RuntimeError(
                "Desktop authorization failed and session cleanup was incomplete."
            ) from exc

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

    def create_backup(
        self, destination: Path, passphrase: str, *, include_memory_key: bool = False,
    ) -> Any:
        return self._specialist_call(
            "create_backup", destination, passphrase,
            include_memory_key=include_memory_key,
        )

    def restore_backup(
        self, backup: Path, destination: Path, passphrase: str, *,
        import_memory_key: bool = False,
    ) -> Any:
        return self._specialist_call(
            "restore_backup", backup, destination, passphrase,
            import_memory_key=import_memory_key,
        )

    def logout(self) -> bool:
        """Revoke the old authenticated controller, then close shared chat state."""
        with self._logout_condition:
            # Close the authorization gate immediately. Concurrent callers wait
            # for the active cleanup; after a failed attempt, a later caller may
            # retry instead of being blocked by a permanently closed bridge.
            self._closed = True
            while self._active_operations:
                self._logout_condition.wait()
            while self._logout_in_progress:
                self._logout_condition.wait()
            if self._logout_complete:
                return False
            self._logout_in_progress = True
        errors: list[Exception] = []
        revoked = False
        try:
            if not self._specialist_logout_complete:
                try:
                    revoked = bool(self._specialists.logout())
                    self._specialist_logout_complete = True
                except Exception as exc:
                    errors.append(exc)
            if not self._chat_logout_complete:
                try:
                    self._chat.logout()
                    self._chat_logout_complete = True
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise RuntimeError(
                    "Desktop logout could not complete every controller cleanup step."
                ) from errors[0]
            with self._logout_condition:
                self._logout_complete = True
            return revoked
        finally:
            with self._logout_condition:
                self._logout_in_progress = False
                self._logout_condition.notify_all()

    close = logout

    def _ensure_open(self) -> None:
        with self._logout_condition:
            if self._closed:
                raise RuntimeError("desktop controller bridge is closed.")
