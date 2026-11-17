"""Authenticated, consent-bound lifecycle for the read-only desktop security monitor.

This controller owns authorization and session cancellation, not sensor
construction. Call snapshot() from a background worker; stop() invalidates
in-flight results before asking the monitor to disable itself.
"""
from __future__ import annotations

from threading import Condition, RLock
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
        self._condition = Condition(self._lock)
        self._active = False
        self._starting = False
        self._starting_generation = None
        self._stopping = False
        self._cleanup_required = False
        self._closed = False
        self._generation = 0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active and not self._starting and not self._closed

    @property
    def starting(self) -> bool:
        with self._lock:
            return self._starting and not self._closed

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
            if self._starting or self._stopping:
                raise SecuritySessionError("Security monitoring is still starting or stopping.")
            if self._cleanup_required:
                raise SecuritySessionError("Security monitor cleanup must be retried before restart.")
            if self._active:
                return
            self._generation += 1
            generation = self._generation
            self._active = True
            self._starting = True
            self._starting_generation = generation
        try:
            self._monitor.enable()
        except Exception as exc:
            with self._lock:
                if self._generation == generation:
                    self._active = False
                    self._generation += 1
            # Keep startup reserved until cleanup finishes. Otherwise another
            # Start could race this compensating disable and be disabled by it.
            try:
                self._disable_monitor()
            except Exception:
                pass
            finally:
                with self._condition:
                    if self._starting_generation == generation:
                        self._starting = False
                        self._starting_generation = None
                        self._condition.notify_all()
            raise SecuritySessionError("Security monitoring could not be enabled.") from exc
        with self._lock:
            startup_current = self._active and self._starting and not self._closed and self._generation == generation
        authorization_current = self._authorized() if startup_current else False
        with self._lock:
            startup_current = (
                startup_current and authorization_current and self._active
                and self._starting and not self._closed and self._generation == generation
            )
            if startup_current:
                self._starting = False
                self._starting_generation = None
                self._condition.notify_all()
        if not startup_current:
            # A concurrent Stop/close may have run before enable() returned.
            # Keep subsequent starts blocked until this late enable is disabled.
            try:
                self._disable_monitor()
            except Exception as exc:
                raise SecuritySessionError("Security monitor cleanup failed after startup cancellation.") from exc
            finally:
                with self._condition:
                    if self._starting_generation == generation:
                        self._starting = False
                        self._starting_generation = None
                        self._condition.notify_all()
            raise SecuritySessionError("The authenticated session ended during startup.")

    def _disable_monitor(self) -> None:
        with self._condition:
            while self._stopping:
                self._condition.wait()
            self._stopping = True
        try:
            self._monitor.disable()
        except Exception as exc:
            with self._condition:
                self._stopping = False
                self._cleanup_required = True
                self._condition.notify_all()
            raise SecuritySessionError("Security monitor cleanup failed.") from exc
        with self._condition:
            self._stopping = False
            self._cleanup_required = False
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._active = False
            # Wait for a late enable() to return and complete its compensating
            # disable before Stop reports completion to the desktop owner.
            self._generation += 1
            while self._starting:
                self._condition.wait()
        self._disable_monitor()

    def _revoke_generation(self, generation: int) -> None:
        with self._lock:
            if not self._active or self._generation != generation:
                return
            self._active = False
            self._generation += 1
        self._disable_monitor()

    def snapshot(self) -> dict:
        if not self._authorized():
            self.stop()
            raise SecuritySessionError("The authenticated session is no longer valid.")
        with self._lock:
            if self._closed or not self._active or self._starting:
                raise SecuritySessionError("Security monitoring is not active.")
            generation = self._generation
        try:
            result = self._monitor.snapshot()
        except Exception as exc:
            with self._lock:
                still_current = self._active and self._generation == generation
            if not still_current:
                raise SecuritySessionError("Monitoring stopped during collection.") from exc
            if not self._authorized():
                self._revoke_generation(generation)
                raise SecuritySessionError("Authorization expired during collection.") from exc
            raise
        with self._lock:
            current = self._active and not self._closed and self._generation == generation
        if not current:
            raise SecuritySessionError("Monitoring stopped during collection.")
        if not self._authorized():
            self._revoke_generation(generation)
            raise SecuritySessionError("Authorization expired during collection.")
        if not isinstance(result, dict):
            self._revoke_generation(generation)
            raise SecuritySessionError("The security monitor returned an invalid snapshot.")
        if (result.get("enabled") is not True or result.get("read_only") is not True
                or result.get("persisted") is not False
                or result.get("automated_response") is not False):
            self._revoke_generation(generation)
            raise SecuritySessionError("The security monitor violated its read-only contract.")
        return result

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._active = False
            self._generation += 1
            # Do not return while an in-flight startup could still enable the
            # monitor after shutdown has completed.
            while self._starting:
                self._condition.wait()
        self._disable_monitor()
