"""Composition helpers for authenticated desktop security monitoring."""
from __future__ import annotations

from typing import Any


def create_desktop_security_service(sessions: Any, session_id: str):
    """Bind the opt-in read-only monitor to exactly one authenticated session.

    No process enumeration or sensor construction happens here. The monitor
    factory is invoked only after the user confirms Start in the dashboard.
    """
    if sessions is None or not callable(getattr(sessions, "current_identity", None)):
        raise TypeError("sessions must provide current_identity()")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")

    def authorize() -> bool:
        try:
            identity = sessions.current_identity()
            return identity is not None and identity.session_id == session_id
        except Exception:
            return False

    def monitor_factory():
        from defense.process_monitor import ReadOnlyProcessMonitor
        from defense.security_monitor import DesktopSecurityMonitor

        return DesktopSecurityMonitor(
            process_monitor=ReadOnlyProcessMonitor(max_processes=2048),
            enabled=False,
        )

    from ui.desktop.security_service import DesktopSecurityService

    return DesktopSecurityService(
        authorize=authorize,
        monitor_factory=monitor_factory,
    )
