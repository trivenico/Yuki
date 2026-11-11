"""Bounded, concurrency-safe JWKS cache for trusted OIDC issuers.

The cache is keyed by the exact host-validated JWKS URI. It never serves stale
keys after expiry or a failed refresh. Unknown-kid refreshes are throttled to
limit attacker-triggered outbound requests during key-rotation probing.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import threading
import time
from typing import Callable


class OIDCJWKSCacheError(RuntimeError):
    """Stable JWKS cache failure without response or credential contents."""


class OIDCJWKSCache:
    def __init__(
        self, *, fetcher: Callable[[str], dict], clock: Callable[[], float] = time.monotonic,
        ttl_seconds: int = 300, maximum_entries: int = 32,
        minimum_refresh_interval_seconds: int = 5,
    ):
        if not callable(fetcher):
            raise TypeError("JWKS fetcher must be callable")
        if not callable(clock):
            raise TypeError("JWKS cache clock must be callable")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("JWKS cache TTL must be between 1 and 3600 seconds")
        if type(maximum_entries) is not int or not 1 <= maximum_entries <= 256:
            raise ValueError("JWKS cache capacity must be between 1 and 256")
        if (type(minimum_refresh_interval_seconds) is not int
                or not 1 <= minimum_refresh_interval_seconds <= 60):
            raise ValueError("JWKS refresh throttle must be between 1 and 60 seconds")
        self._fetcher = fetcher
        self._clock = clock
        self._ttl = ttl_seconds
        self._maximum_entries = maximum_entries
        self._refresh_interval = minimum_refresh_interval_seconds
        self._entries = OrderedDict()
        self._last_fetch = {}
        self._lock = threading.RLock()

    def invalidate(self, jwks_uri: str | None = None) -> None:
        """Explicitly discard one issuer's keys or the entire bounded cache."""
        with self._lock:
            if jwks_uri is None:
                self._entries.clear()
                self._last_fetch.clear()
            else:
                self._entries.pop(jwks_uri, None)
                self._last_fetch.pop(jwks_uri, None)

    def get(self, jwks_uri: str, *, force_refresh: bool = False) -> dict:
        if not isinstance(jwks_uri, str) or not jwks_uri or len(jwks_uri) > 2048:
            raise OIDCJWKSCacheError("oidc_jwks_uri_invalid")
        now = self._clock()
        with self._lock:
            entry = self._entries.get(jwks_uri)
            fresh = entry is not None and now < entry[0]
            if fresh and force_refresh:
                last_fetch = self._last_fetch.get(jwks_uri, float("-inf"))
                if now - last_fetch < self._refresh_interval:
                    return deepcopy(entry[1])
                fresh = False
            if fresh:
                self._entries.move_to_end(jwks_uri)
                return deepcopy(entry[1])

            # Fetch while holding the lock: one refresh per URI at a time.
            try:
                document = self._fetcher(jwks_uri)
            except Exception:
                # Do not fall back to an expired or suspect stale key set.
                raise OIDCJWKSCacheError("oidc_jwks_unavailable") from None
            if not isinstance(document, dict):
                raise OIDCJWKSCacheError("oidc_jwks_invalid")
            keys = document.get("keys")
            if not isinstance(keys, list) or not 1 <= len(keys) <= 100:
                raise OIDCJWKSCacheError("oidc_jwks_invalid")
            self._last_fetch[jwks_uri] = now
            self._entries[jwks_uri] = (now + self._ttl, deepcopy(document))
            self._entries.move_to_end(jwks_uri)
            while len(self._entries) > self._maximum_entries:
                evicted_uri, _ = self._entries.popitem(last=False)
                self._last_fetch.pop(evicted_uri, None)
            return deepcopy(document)
