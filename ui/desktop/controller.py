"""Testable desktop companion controller; deliberately independent of Qt."""
from __future__ import annotations

from companion.application import CompanionApplicationService
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity


class DesktopCompanionController:
    """Bind one authenticated local session to the shared companion service."""

    def __init__(
        self,
        application: CompanionApplicationService,
        identity: AuthenticatedDesktopIdentity,
        sessions: DesktopSessionManager,
    ) -> None:
        if not isinstance(application, CompanionApplicationService):
            raise TypeError("application must be a CompanionApplicationService.")
        if not isinstance(identity, AuthenticatedDesktopIdentity):
            raise TypeError("identity must be an authenticated desktop identity.")
        if not isinstance(sessions, DesktopSessionManager):
            raise TypeError("sessions must be a DesktopSessionManager.")
        current = sessions.current_identity()
        if current != identity:
            raise PermissionError("The desktop session is no longer authenticated.")
        self.application = application
        self.identity = identity
        self.sessions = sessions

    def respond(self, message: str) -> str:
        if self.sessions.current_identity() != self.identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        return self.application.respond(message).message

    def set_memory_context(self, enabled: bool) -> None:
        if self.sessions.current_identity() != self.identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        if enabled:
            self.application.memory.enable_context()
        else:
            self.application.memory.disable_context()

    def memory_context_enabled(self) -> bool:
        if self.sessions.current_identity() != self.identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        return self.application.memory.context_enabled

    def logout(self) -> bool:
        self.application.reset_conversation()
        self.application.memory.disable()
        return self.sessions.logout()
