"""Dependency-injected desktop composition boundary.

This module intentionally has no Qt imports and does not discover identity from
environment variables or UI fields. The trusted launcher must supply an
authenticator; failure to authenticate prevents desktop composition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from companion.application import CompanionApplicationService


class DesktopStartupError(RuntimeError):
    """Raised when desktop startup cannot establish a trusted session."""


@dataclass(frozen=True)
class AuthenticatedDesktopIdentity:
    """Identity established by a trusted local authentication adapter."""

    actor_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.actor_id, str) or not self.actor_id.strip():
            raise ValueError("Authenticated actor identity is required.")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("Authenticated session identity is required.")


@dataclass(frozen=True)
class DesktopContext:
    """Narrow set of dependencies exposed to desktop controllers."""

    application: CompanionApplicationService
    identity: AuthenticatedDesktopIdentity


def build_desktop_context(
    *,
    authenticate: Callable[[], AuthenticatedDesktopIdentity],
    provider: Any,
    memory: Any = None,
    session: Any = None,
    agent_coordinator: Any = None,
    task_runtime: Any = None,
) -> DesktopContext:
    """Authenticate first, then compose the shared companion application.

    The authenticator is trusted infrastructure, not a callback supplied by a
    widget. Actor and session IDs are never accepted from presentation inputs.
    """
    if not callable(authenticate):
        raise DesktopStartupError("A trusted local authenticator is required.")
    try:
        identity = authenticate()
    except Exception as exc:
        raise DesktopStartupError(
            "Yuki desktop could not authenticate this local session."
        ) from exc
    if not isinstance(identity, AuthenticatedDesktopIdentity):
        raise DesktopStartupError("The local authenticator returned an invalid identity.")

    try:
        application = CompanionApplicationService(
            provider,
            memory=memory,
            session=session,
            agent_coordinator=agent_coordinator,
            task_runtime=task_runtime,
        )
    except Exception as exc:
        raise DesktopStartupError(
            "Yuki desktop could not initialize its application services."
        ) from exc
    return DesktopContext(application=application, identity=identity)
