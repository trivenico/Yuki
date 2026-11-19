"""Metadata-only observation source contract and deterministic fake source.

No operating-system capture, OCR, browser scraping, or tool execution lives here.
Adapters must submit bounded metadata events to an authorization gateway.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading
import time
from types import MappingProxyType
from typing import Mapping, Optional, Protocol, runtime_checkable

from core.observation_policy import ObservationSession, _normalize_identifier


@dataclass(frozen=True)
class ObservationEvent:
    source: str
    category: str
    metadata: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "source", _normalize_identifier(self.source, "source"))
        object.__setattr__(self, "category", _normalize_identifier(self.category, "category"))
        if not isinstance(self.metadata, Mapping) or len(self.metadata) > 24:
            raise ValueError("metadata must be a mapping with at most 24 fields")
        clean = {}
        for key, value in self.metadata.items():
            if not isinstance(key, str) or not key or len(key) > 64:
                raise ValueError("metadata keys must be bounded non-empty strings")
            if isinstance(value, str):
                if len(value) > 512 or "\x00" in value:
                    raise ValueError("metadata text must be at most 512 characters")
            elif value is not None and type(value) not in (bool, int, float):
                raise ValueError("metadata values must be scalar text, numbers, booleans, or null")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("metadata numbers must be finite")
            clean[key] = value
        object.__setattr__(self, "metadata", MappingProxyType(clean))


@runtime_checkable
class ObservationSource(Protocol):
    """A pull-based metadata event source. Implementations must be bounded."""

    @property
    def source_id(self) -> str:
        ...

    def read_next(self, timeout: float = 0.0) -> Optional[ObservationEvent]:
        ...


class FakeObservationSource:
    """In-memory test/development source; never reads the host desktop."""

    def __init__(self, source_id: str, *, capacity: int = 128):
        self._source_id = _normalize_identifier(source_id, "source")
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("capacity must be an integer in 1..4096")
        self._capacity = capacity
        self._events = deque()
        self._condition = threading.Condition()
        self._closed = False

    @property
    def source_id(self) -> str:
        return self._source_id

    def publish(self, event: ObservationEvent) -> bool:
        if not isinstance(event, ObservationEvent) or event.source != self._source_id:
            raise ValueError("event must belong to this source")
        with self._condition:
            if self._closed or len(self._events) >= self._capacity:
                return False
            self._events.append(event)
            self._condition.notify()
            return True

    def read_next(self, timeout: float = 0.0) -> Optional[ObservationEvent]:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("timeout must be between 0 and 30 seconds")
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._events and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._events.popleft() if self._events else None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._events.clear()
            self._condition.notify_all()


class ObservationGateway:
    """Authorizes events before forwarding metadata to an in-process consumer."""

    def __init__(self, session: ObservationSession):
        if not isinstance(session, ObservationSession):
            raise TypeError("session must be an ObservationSession")
        self._session = session

    def poll(
        self, source: ObservationSource, consumer, *, timeout: float = 0.0,
        on_authorized=None,
    ) -> bool:
        if not isinstance(source, ObservationSource):
            raise TypeError("source must implement ObservationSource")
        if not callable(consumer):
            raise TypeError("consumer must be callable")
        if on_authorized is not None and not callable(on_authorized):
            raise TypeError("on_authorized must be callable")
        event = source.read_next(timeout)
        if event is None:
            return False
        if event.source != source.source_id:
            return False
        if not self._session.authorize(event.source, event.category):
            return False
        if on_authorized is not None:
            on_authorized(event)
        consumer(event)
        return True
