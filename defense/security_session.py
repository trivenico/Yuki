"""Authenticated, consent-bound lifecycle for the read-only desktop security monitor.

This controller owns authorization and session cancellation, not sensor
construction. Call snapshot() from a background worker; stop() invalidates
in-flight results before asking the monitor to disable itself.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable, Any


class SecuritySessionError(RuntimeError):
    """The security session is not authorized or no longer active."""


class SecuritySessionController:
    def __init__(self, monitor: Any, *, authorize: Callable[[], bool]):
        if not callable(getattr(monitor, "enable", None)):
            raise TypeError("monitor must provide enable()")
        if not callable(getattr(monitor, "disable", None)):
            raise TypeError("monitor must provide disable()")
        if not callable(getattr(monitor, "snapshot", None)):
            raise TypeError("monitor must provide snapshot()")
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        self._monitor = monitor
        self._authorize = authorize
        self._lock = RLock()
        self._active = False
        self._closed = False
        self._generation = 0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active and not self._closed

    def _authorized(self) -> bool:
        try:
            return self._authorize() is True
        except Exception:
            return False

    def start(self, *, consent: bool) -> None:
        if consent is not True:
            raise SecuritySessionError("Explicit monitoring consent is required.")
        if not self._authorized():
            raise SecuritySessionError("An authenticated desktop session is required.")
        with self._lock:
            if self._closed:
                raise SecuritySessionError("The security session controller is closed.")
            if self._active:
                return
            self._generation += 1
            generation = self._generation
            self._active = True
        try:
            self._monitor.enable()
        except Exception as exc:
            with self._lock:
                if self._generation == generation:
                    self._active = False
                    self._generation += 1
            try:
                self._monitor.disable()
            except Exception:
                pass
            raise SecuritySessionError("Security monitoring could not be enabled.") from exc
        if not self._authorized():
            self.stop()
            raise SecuritySessionError("The authenticated session expired during startup.")

    def stop(self) -> None:
        with self._lock:
            was_active = self._active
            self._active = False
            self._generation += 1
        # Invalidate controller results first; disable must not hold our lock
        # while a sensor is blocked.
        try:
            self._monitor.disable()
        except Exception as exc:
            raise SecuritySessionError("Security monitor cleanup failed.") from exc

    def snapshot(self) -> dict:
        if not self._authorized():
            self.stop()
            raise SecuritySessionError("The authenticated session is no longer valid.")
        with self._lock:
            if self._closed or not self._active:
                raise SecuritySessionError("Security monitoring is not active.")
            generation = self._generation
        try:
            result = self._monitor.snapshot()
        except Exception as exc:
            with self._lock:
                still_current = self._active and self._generation == generation
            if not still_current:
                raise SecuritySessionError("Monitoring stopped during collection.") from exc
            raise
        with self._lock:
            current = self._active and not self._closed and self._generation == generation
        if not current or not self._authorized():
            self.stop()
            raise SecuritySessionError("Monitoring stopped or authorization expired during collection.")
        if not isinstance(result, dict):
            raise SecuritySessionError("The security monitor returned an invalid snapshot.")
        if (result.get("enabled") is not True or result.get("read_only") is not True
                or result.get("persisted") is not False
                or result.get("automated_response") is not False):
            raise SecuritySessionError("The security monitor violated its read-only contract.")
        return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._active = False
            self._generation += 1
        try:
            self._monitor.disable()
        except Exception as exc:
            raise SecuritySessionError("Security monitor cleanup failed.") from exc
