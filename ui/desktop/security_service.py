"""Lazy composition boundary for opt-in desktop security monitoring.

This module deliberately contains no Qt imports and creates no monitor or
sensor until an authenticated user explicitly starts monitoring. The desktop
window can call these methods from bounded background workers.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable


class SecuritySessionError(RuntimeError):
    """Security consent or authenticated-session requirements were not met."""


class DesktopSecurityServiceError(RuntimeError):
    """The desktop security service could not complete a requested operation."""


class DesktopSecurityService:
    """Own one authenticated security session and lazily-created monitor.

    `monitor_factory` must construct only read-only, non-persistent components.
    It is not called by construction, status inspection, snapshot, stop, or
    close when monitoring was never started.
    """

    def __init__(
        self,
        *,
        authorize: Callable[[], bool],
        monitor_factory: Callable[[], Any],
        controller_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        if not callable(monitor_factory):
            raise TypeError("monitor_factory must be callable")
        self._authorize = authorize
        self._monitor_factory = monitor_factory
        self._controller_factory = controller_factory
        self._controller: Any | None = None
        self._lock = RLock()
        self._closed = False
        self._generation = 0

    @property
    def active(self) -> bool:
        with self._lock:
            controller = self._controller
            return not self._closed and controller is not None and controller.active

    @property
    def configured(self) -> bool:
        """Whether the monitor has been constructed; this does not imply active."""
        with self._lock:
            return self._controller is not None

    def start(self, *, consent: bool) -> None:
        if consent is not True:
            raise SecuritySessionError("Explicit monitoring consent is required.")
        try:
            authorized = self._authorize() is True
        except Exception:
            authorized = False
        if not authorized:
            raise SecuritySessionError("An authenticated desktop session is required.")

        # Serialize startup with stop/close so a late enable cannot undo stop.
        with self._lock:
            if self._closed:
                raise SecuritySessionError("The desktop security service is closed.")
            controller = self._controller
            if controller is None:
                monitor = self._monitor_factory()
                factory = self._controller_factory
                if factory is None:
                    # Import only when explicit consent starts monitoring.
                    from defense.security_session import SecuritySessionController
                    factory = SecuritySessionController
                controller = factory(monitor, authorize=self._authorize)
                self._controller = controller
            self._generation += 1
            try:
                controller.start(consent=True)
            except Exception as exc:
                # Keep the controller reachable if compensating cleanup failed.
                raise DesktopSecurityServiceError(
                    "Desktop security monitoring could not be started."
                ) from exc

    def snapshot(self) -> dict:
        with self._lock:
            if self._closed or self._controller is None:
                raise SecuritySessionError("Desktop security monitoring is not active.")
            controller = self._controller
            generation = self._generation
        result = controller.snapshot()
        with self._lock:
            if self._closed or generation != self._generation or controller is not self._controller:
                raise SecuritySessionError("The desktop security session changed during refresh.")
        return result

    def stop(self) -> None:
        # Keep stop serialized with a concurrent start so monitoring cannot
        # silently restart after the user presses Stop.
        with self._lock:
            self._generation += 1
            controller = self._controller
            if controller is not None:
                controller.stop()

    def close(self) -> None:
        # Keep close serialized with stop/start. If controller cleanup fails,
        # _closed remains true and a later close() can retry the same handle.
        with self._lock:
            if not self._closed:
                self._closed = True
                self._generation += 1
            controller = self._controller
            if controller is not None:
                controller.close()



def create_authenticated_desktop_security_service(sessions: Any, identity: Any) -> DesktopSecurityService:
    """Create a lazy read-only monitor bound to one exact desktop login identity.

    No sensor or optional process-inspection dependency is imported until the
    user explicitly starts monitoring and the monitor factory is invoked.
    """
    current_identity = getattr(sessions, "current_identity", None)
    if not callable(current_identity) or identity is None:
        raise TypeError("An authenticated desktop session identity is required.")

    def authorize() -> bool:
        try:
            return current_identity() == identity
        except Exception:
            return False

    def monitor_factory():
        from defense.process_monitor import ReadOnlyProcessMonitor
        from defense.security_monitor import DesktopSecurityMonitor
        return DesktopSecurityMonitor(
            process_monitor=ReadOnlyProcessMonitor(max_processes=2048),
        )

    return DesktopSecurityService(
        authorize=authorize,
        monitor_factory=monitor_factory,
    )
