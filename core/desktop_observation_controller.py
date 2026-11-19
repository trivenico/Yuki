"""Consent-bound orchestration for desktop metadata observation.

This controller joins the platform collector, allowlisted metadata adapter,
and observation session manager. It is caller-polled and never starts a thread.
"""
from __future__ import annotations

from functools import wraps
import threading
from typing import Callable, Optional

from core.desktop_metadata_adapter import DesktopMetadataAdapter
from core.desktop_windows import WindowsForegroundAppCollector
from core.observation_policy import ObservationPolicy
from core.observation_session_manager import ObservationSessionManager


def _serialized(method):
    """Serialize lifecycle transitions and polling on one controller instance."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class DesktopObservationController:
    """Own one explicit desktop observation session and its lifecycle."""

    def __init__(
        self,
        policy: ObservationPolicy,
        executable_to_app_id: dict[str, str],
        *,
        indicator: Callable[[bool], None],
        manager: Optional[ObservationSessionManager] = None,
        reader=None,
        event_limit: int = 1000,
    ):
        if not isinstance(policy, ObservationPolicy):
            raise TypeError("policy must be an ObservationPolicy")
        if not callable(indicator):
            raise ValueError("a visible indicator callback is required")
        if not isinstance(executable_to_app_id, dict):
            raise ValueError("executable_to_app_id must be a mapping")
        if not policy.enabled or "desktop:metadata" not in policy.allowed_sources:
            raise PermissionError("policy must enable and allow desktop:metadata")
        self._lock = threading.RLock()
        self._policy = policy
        self._mapping = dict(executable_to_app_id)
        self._indicator_callback = indicator
        self._manager = manager or ObservationSessionManager()
        if not isinstance(self._manager, ObservationSessionManager):
            raise TypeError("manager must be an ObservationSessionManager")
        self._reader = reader
        if type(event_limit) is not int or not 1 <= event_limit <= 1_000_000:
            raise ValueError("event_limit must be an integer in 1..1000000")
        self._event_limit = event_limit
        self._session_id = None
        self._sink = None
        self._collector = None
        self._state = "stopped"

    @property
    def session_id(self):
        return self._session_id

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def status(self):
        """Return the manager's bounded counters for the active observation session."""
        with self._lock:
            if self._session_id is None:
                return None
            return self._manager.status(self._session_id)

    def _set_indicator(self, active: bool) -> None:
        self._indicator_callback(active)

    @_serialized
    def start(self, *, consent_granted: bool) -> str:
        if self._state != "stopped":
            raise RuntimeError("desktop observation controller has already been started")
        if consent_granted is not True:
            raise PermissionError("explicit desktop observation consent is required")
        session_id = self._manager.create_session(
            self._policy,
            consent_granted=True,
            event_limit=self._event_limit,
        )
        sink = DesktopMetadataAdapter(
            allowed_app_ids=tuple(self._mapping.values()),
        )
        collector_args = {"reader": self._reader} if self._reader is not None else {}
        collector = WindowsForegroundAppCollector(self._mapping, **collector_args)
        try:
            self._manager.register_source(session_id, sink)
            collector.start(
                sink,
                consent_granted=True,
                indicator=self._set_indicator,
            )
        except Exception:
            collector.stop()
            try:
                self._set_indicator(False)
            except Exception:
                pass
            sink.close()
            self._manager.revoke(session_id)
            raise
        self._session_id = session_id
        self._sink = sink
        self._collector = collector
        self._state = "active"
        return session_id

    @_serialized
    def poll_once(self, consumer: Callable) -> bool:
        if not callable(consumer):
            raise ValueError("consumer must be callable")
        if self._state != "active":
            return False
        source = self._collector
        sink = self._sink
        session_id = self._session_id
        if not source.poll_once():
            return False
        try:
            delivered = self._manager.poll(
                session_id, sink.source_id, consumer, timeout=0.0,
            )
        finally:
            # The manager can exhaust the budget before re-raising a consumer
            # exception. Always reconcile collector and visible indicator state.
            if self._manager.status(session_id).state == "limit_reached":
                self._state = "limit_reached"
                self._collector.stop()
        return delivered

    @_serialized
    def pause(self) -> None:
        if self._state != "active":
            return
        self._manager.pause(self._session_id)
        self._state = "paused"
        try:
            self._set_indicator(False)
        except Exception:
            self.revoke()
            raise

    @_serialized
    def resume(self) -> bool:
        if self._state != "paused":
            return False
        if not self._manager.resume(self._session_id):
            return False
        try:
            self._set_indicator(True)
        except Exception:
            self.revoke()
            raise
        self._state = "active"
        return True

    @_serialized
    def revoke(self) -> None:
        if self._state in ("revoked", "stopped"):
            return
        self._state = "revoked"
        try:
            self._collector.stop()
        finally:
            self._manager.revoke(self._session_id)
            self._sink = None
