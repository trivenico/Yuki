"""Bounded in-memory handoff for authorized observation events.

The observation manager invokes consumers while holding a session lock. This
buffer gives that consumer a constant-time, non-blocking handoff so expensive
classification or learning work can run elsewhere.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional

from core.observation_sources import ObservationEvent


class ObservationEventBuffer:
    """Thread-safe bounded queue; never persists events or blocks producers."""

    def __init__(self, *, capacity: int = 128):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("capacity must be an integer in 1..4096")
        self._capacity = capacity
        self._events = deque()
        self._lock = threading.Lock()
        self._closed = False

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def __call__(self, event: ObservationEvent) -> None:
        """Accept one event without waiting; compatible with manager.poll()."""
        if not isinstance(event, ObservationEvent):
            raise TypeError("event must be an ObservationEvent")
        with self._lock:
            if self._closed:
                raise RuntimeError("observation event buffer is closed")
            if len(self._events) >= self._capacity:
                raise BufferError("observation event buffer is full")
            self._events.append(event)

    def get_nowait(self) -> Optional[ObservationEvent]:
        with self._lock:
            return self._events.popleft() if self._events else None

    def drain(self, *, limit: int = 32) -> tuple[ObservationEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= self._capacity:
            raise ValueError("limit must be an integer in 1..capacity")
        with self._lock:
            count = min(limit, len(self._events))
            return tuple(self._events.popleft() for _ in range(count))

    def close(self, *, clear: bool = True) -> None:
        with self._lock:
            self._closed = True
            if clear:
                self._events.clear()
