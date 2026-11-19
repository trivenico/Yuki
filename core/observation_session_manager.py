"""Bounded lifecycle manager for consented observation sessions.

The manager exposes status for a desktop indicator, limits source/event counts,
and closes source queues on revocation. It does not capture desktop content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import uuid
from typing import Callable, Optional

from core.observation_policy import ObservationPolicy, ObservationSession
from core.observation_sources import ObservationGateway, ObservationSource


@dataclass(frozen=True)
class ObservationSessionStatus:
    session_id: str
    state: str
    indicator_active: bool
    registered_sources: int
    delivered_events: int
    processed_events: int
    delivery_failures: int
    event_limit: int


@dataclass
class _ManagedSession:
    session: ObservationSession
    gateway: ObservationGateway
    event_limit: int
    sources: dict = field(default_factory=dict)
    delivered_events: int = 0
    processed_events: int = 0
    delivery_failures: int = 0
    state_override: Optional[str] = None
    lock: threading.RLock = field(default_factory=threading.RLock)


class ObservationSessionManager:
    """In-process session registry with explicit consent and bounded resources."""

    def __init__(self, *, max_sessions: int = 32, max_sources_per_session: int = 16):
        if type(max_sessions) is not int or not 1 <= max_sessions <= 256:
            raise ValueError("max_sessions must be an integer in 1..256")
        if type(max_sources_per_session) is not int or not 1 <= max_sources_per_session <= 64:
            raise ValueError("max_sources_per_session must be an integer in 1..64")
        self._max_sessions = max_sessions
        self._max_sources = max_sources_per_session
        self._sessions = {}
        self._lock = threading.RLock()

    def create_session(
        self,
        policy: ObservationPolicy,
        *,
        consent_granted: bool,
        event_limit: int = 1000,
        audit_sink: Optional[Callable[[dict], None]] = None,
    ) -> str:
        if type(event_limit) is not int or not 1 <= event_limit <= 1_000_000:
            raise ValueError("event_limit must be an integer in 1..1000000")
        with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise RuntimeError("observation session capacity reached")
            session_id = uuid.uuid4().hex
            session = ObservationSession(
                policy, consent_granted=consent_granted, audit_sink=audit_sink,
            )
            self._sessions[session_id] = _ManagedSession(
                session=session,
                gateway=ObservationGateway(session),
                event_limit=event_limit,
            )
            return session_id

    def _get(self, session_id: str) -> _ManagedSession:
        if not isinstance(session_id, str):
            raise KeyError("unknown observation session")
        with self._lock:
            managed = self._sessions.get(session_id)
        if managed is None:
            raise KeyError("unknown observation session")
        return managed

    def register_source(self, session_id: str, source: ObservationSource) -> None:
        managed = self._get(session_id)
        if not isinstance(source, ObservationSource):
            raise TypeError("source must implement ObservationSource")
        with managed.lock:
            if managed.state_override is not None or managed.session.state not in ("active", "paused"):
                raise RuntimeError("observation session is not accepting sources")
            source_id = source.source_id
            if not managed.session.is_source_allowlisted(source_id):
                raise PermissionError("source is not in the session allowlist")
            if source_id in managed.sources:
                raise ValueError("source is already registered")
            if len(managed.sources) >= self._max_sources:
                raise RuntimeError("observation source capacity reached")
            managed.sources[source_id] = source

    def poll(
        self,
        session_id: str,
        source_id: str,
        consumer,
        *,
        timeout: float = 0.0,
    ) -> bool:
        managed = self._get(session_id)
        if not isinstance(source_id, str) or not callable(consumer):
            raise ValueError("source_id and consumer are required")
        if timeout != 0:
            raise ValueError("session manager polling is non-blocking; sources should buffer events")
        with managed.lock:
            if managed.state_override is not None or managed.session.state != "active":
                return False
            if managed.delivered_events >= managed.event_limit:
                managed.state_override = "limit_reached"
                self._close_sources(managed)
                return False
            source = managed.sources.get(source_id)
            if source is None:
                raise KeyError("source is not registered")
            def dispatch(event):
                # The gateway calls this only after source and policy authorization.
                # Count the attempt before consumer code can fail.
                managed.processed_events += 1
                try:
                    consumer(event)
                except Exception:
                    managed.delivery_failures += 1
                    raise
                managed.delivered_events += 1

            try:
                delivered = managed.gateway.poll(source, dispatch, timeout=timeout)
            except Exception:
                if managed.processed_events >= managed.event_limit:
                    managed.state_override = "limit_reached"
                    self._close_sources(managed)
                raise
            if delivered and managed.processed_events >= managed.event_limit:
                managed.state_override = "limit_reached"
                self._close_sources(managed)
            return delivered

    @staticmethod
    def _close_sources(managed: _ManagedSession) -> None:
        sources, managed.sources = tuple(managed.sources.values()), {}
        for source in sources:
            close = getattr(source, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # Revocation must remain effective even if adapter cleanup fails.
                    pass

    def pause(self, session_id: str) -> None:
        managed = self._get(session_id)
        with managed.lock:
            if managed.state_override is None:
                managed.session.pause()

    def resume(self, session_id: str) -> bool:
        managed = self._get(session_id)
        with managed.lock:
            if managed.state_override is not None:
                return False
            return managed.session.resume()

    def revoke(self, session_id: str) -> None:
        managed = self._get(session_id)
        with managed.lock:
            managed.session.revoke()
            managed.state_override = "revoked"
            self._close_sources(managed)

    def status(self, session_id: str) -> ObservationSessionStatus:
        managed = self._get(session_id)
        with managed.lock:
            state = managed.state_override or managed.session.state
            return ObservationSessionStatus(
                session_id=session_id,
                state=state,
                indicator_active=state == "active",
                registered_sources=len(managed.sources),
                delivered_events=managed.delivered_events,
                processed_events=managed.processed_events,
                delivery_failures=managed.delivery_failures,
                event_limit=managed.event_limit,
            )
