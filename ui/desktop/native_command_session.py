"""Authenticated desktop facade for the native command runtime."""
from __future__ import annotations

from typing import Any

from core.native_command_runtime import NativeCommandRuntime


class AuthenticatedNativeCommandSession:
    """Revalidate the live desktop session before every native command."""

    def __init__(self, runtime: NativeCommandRuntime, *, identity: Any, sessions: Any) -> None:
        if not isinstance(runtime, NativeCommandRuntime):
            raise TypeError("runtime must be a NativeCommandRuntime.")
        actor_id = getattr(identity, "actor_id", None)
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 128:
            raise ValueError("identity must contain a valid authenticated actor_id.")
        if not callable(getattr(sessions, "current_identity", None)):
            raise TypeError("sessions must provide current_identity().")
        if sessions.current_identity() != identity:
            raise PermissionError("The desktop session is no longer authenticated.")
        self._runtime = runtime
        self._identity = identity
        self._sessions = sessions

    def dispatch(self, text: str, *, confirmed: bool = False):
        if self._sessions.current_identity() != self._identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        return self._runtime.dispatch(
            authenticated_actor_id=self._identity.actor_id,
            text=text,
            confirmed=confirmed,
        )


__all__ = ["AuthenticatedNativeCommandSession"]
