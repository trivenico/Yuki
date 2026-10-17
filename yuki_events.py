"""Small synchronous in-process event bus for agent lifecycle events."""

from collections import defaultdict
from collections.abc import Callable
from threading import RLock


class EventBus:
    """Publish events to local subscribers; callback failures are isolated."""

    def __init__(self):
        self._subscribers = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event_name, callback):
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError("Event name is required.")
        if not callable(callback):
            raise TypeError("Callback must be callable.")
        with self._lock:
            self._subscribers[event_name].append(callback)

        def unsubscribe():
            with self._lock:
                callbacks = self._subscribers.get(event_name, [])
                if callback in callbacks:
                    callbacks.remove(callback)
                if not callbacks:
                    self._subscribers.pop(event_name, None)
        return unsubscribe

    def publish(self, event_name, payload=None):
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError("Event name is required.")
        with self._lock:
            callbacks = tuple(self._subscribers.get(event_name, ()))
        delivered = 0
        for callback in callbacks:
            try:
                callback(payload if payload is not None else {})
                delivered += 1
            except Exception:
                # One subscriber must not prevent delivery to other subscribers.
                continue
        return delivered
