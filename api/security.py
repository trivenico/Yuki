"""Bounded inbound ASGI security middleware for Yuki's HTTP surface.

Client identity is taken only from the ASGI transport peer address. Forwarded
headers are intentionally ignored until an explicit trusted-proxy boundary is
configured by the deployment.
"""
from __future__ import annotations

import ipaddress
import json
import math
import threading
import time
from collections.abc import Callable


class RateLimitConfigurationError(ValueError):
    """Invalid inbound rate-limit configuration."""


class FixedWindowRateLimiter:
    """Thread-safe, bounded per-peer fixed-window limiter."""

    def __init__(
        self, *, max_requests: int = 120, window_seconds: int = 60,
        max_clients: int = 10_000, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(max_requests) is not int or not 1 <= max_requests <= 100_000:
            raise RateLimitConfigurationError("max_requests must be between 1 and 100000")
        if type(window_seconds) is not int or not 1 <= window_seconds <= 3600:
            raise RateLimitConfigurationError("window_seconds must be between 1 and 3600")
        if type(max_clients) is not int or not 1 <= max_clients <= 1_000_000:
            raise RateLimitConfigurationError("max_clients must be between 1 and 1000000")
        if not callable(clock):
            raise RateLimitConfigurationError("clock must be callable")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._clock = clock
        self._buckets: dict[str, tuple[int, int]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def normalize_peer(peer: object) -> str:
        if not isinstance(peer, (tuple, list)) or not peer:
            return "unknown"
        raw = peer[0]
        if not isinstance(raw, str) or len(raw) > 64:
            return "unknown"
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            return "unknown"
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return str(address)

    def allow(self, peer: object) -> tuple[bool, int]:
        key = self.normalize_peer(peer)
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise RuntimeError("rate_limit_clock_invalid")
        window = int(now // self.window_seconds)
        retry_after = max(1, self.window_seconds - int(now % self.window_seconds))
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                expired = [item for item, value in self._buckets.items() if value[0] < window]
                for item in expired:
                    del self._buckets[item]
                if len(self._buckets) >= self.max_clients:
                    return False, retry_after
                self._buckets[key] = (window, 1)
                return True, 0
            if bucket[0] != window:
                self._buckets[key] = (window, 1)
                return True, 0
            if bucket[1] >= self.max_requests:
                return False, retry_after
            self._buckets[key] = (window, bucket[1] + 1)
            return True, 0

    def close(self) -> None:
        with self._lock:
            self._buckets.clear()


class APISecurityMiddleware:
    """Apply peer-based rate limits and baseline browser security headers."""

    _SECURITY_HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"cross-origin-resource-policy", b"same-origin"),
        (b"content-security-policy",
         b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
         b"img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
    )

    def __init__(self, app, *, limiter: FixedWindowRateLimiter | None = None):
        if not callable(app):
            raise TypeError("app must be callable")
        if limiter is not None and not isinstance(limiter, FixedWindowRateLimiter):
            raise TypeError("limiter must be a FixedWindowRateLimiter")
        self.app = app
        self.limiter = limiter or FixedWindowRateLimiter()

    def close(self) -> None:
        """Release process-local limiter state during runtime shutdown."""
        self.limiter.close()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        allowed, retry_after = self.limiter.allow(scope.get("client"))
        if not allowed:
            body = json.dumps({"error": "rate_limit_exceeded"}).encode("utf-8")
            headers = [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"retry-after", str(retry_after).encode("ascii")),
                *self._SECURITY_HEADERS,
            ]
            await send({"type": "http.response.start", "status": 429, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_security_headers(message):
            if message.get("type") == "http.response.start":
                headers = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() not in {item[0] for item in self._SECURITY_HEADERS}
                ]
                headers.extend(self._SECURITY_HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
