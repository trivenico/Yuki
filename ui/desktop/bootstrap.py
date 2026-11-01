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
        for field_name in ("actor_id", "session_id"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError(f"Authenticated {field_name.replace('_', ' ')} is invalid.")
            object.__setattr__(self, field_name, value.strip())


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

    This factory does not implement OS authentication itself. Production callers
    must inject an authenticator backed by a reviewed local login/session provider
    and compose coordinators/runtimes with authorization callbacks bound to that
    identity. A caller-created identity object is not, by itself, proof of login.
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
