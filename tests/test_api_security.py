import unittest

from api.security import APISecurityMiddleware, FixedWindowRateLimiter, RateLimitConfigurationError


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FixedWindowRateLimiterTests(unittest.TestCase):
    def test_enforces_per_peer_limit_and_resets_on_next_window(self):
        clock = Clock()
        limiter = FixedWindowRateLimiter(max_requests=2, window_seconds=10, clock=clock)
        self.assertEqual(limiter.allow(("192.0.2.1", 1000)), (True, 0))
        self.assertEqual(limiter.allow(("192.0.2.1", 1000)), (True, 0))
        allowed, retry = limiter.allow(("192.0.2.1", 1000))
        self.assertFalse(allowed)
        self.assertEqual(retry, 10)
        clock.value = 10
        self.assertEqual(limiter.allow(("192.0.2.1", 1000)), (True, 0))

    def test_clients_are_isolated_and_forwarded_headers_are_not_used(self):
        limiter = FixedWindowRateLimiter(max_requests=1)
        self.assertTrue(limiter.allow(("192.0.2.1", 1000))[0])
        self.assertTrue(limiter.allow(("192.0.2.2", 1000))[0])

    def test_peer_addresses_are_normalized(self):
        self.assertEqual(FixedWindowRateLimiter.normalize_peer(("::ffff:192.0.2.1", 1)), "192.0.2.1")
        self.assertEqual(FixedWindowRateLimiter.normalize_peer(("bad peer", 1)), "unknown")

    def test_bounded_client_table_fails_closed_when_full(self):
        limiter = FixedWindowRateLimiter(max_clients=1)
        self.assertTrue(limiter.allow(("192.0.2.1", 1))[0])
        self.assertFalse(limiter.allow(("192.0.2.2", 1))[0])

    def test_configuration_is_strict(self):
        for kwargs in ({"max_requests": True}, {"window_seconds": 0}, {"max_clients": 0}, {"clock": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RateLimitConfigurationError):
                FixedWindowRateLimiter(**kwargs)


class APISecurityMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limits_and_adds_security_headers_to_responses(self):
        calls = []

        async def app(scope, receive, send):
            calls.append(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = APISecurityMiddleware(
            app, limiter=FixedWindowRateLimiter(max_requests=1, window_seconds=60),
        )
        messages = []

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "method": "GET", "path": "/", "client": ("192.0.2.10", 1234)}
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await middleware(scope, receive, send)
        start = messages[0]
        headers = dict(start["headers"])
        self.assertEqual(start["status"], 200)
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertEqual(headers[b"x-frame-options"], b"DENY")

        messages.clear()
        await middleware(scope, receive, send)
        self.assertEqual(messages[0]["status"], 429)
        self.assertEqual(dict(messages[0]["headers"])[b"retry-after"], b"60")
        self.assertEqual(len(calls), 1)

    async def test_forwarded_for_spoofing_does_not_change_peer_identity(self):
        calls = []

        async def app(scope, receive, send):
            calls.append(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = APISecurityMiddleware(
            app, limiter=FixedWindowRateLimiter(max_requests=1, window_seconds=60),
        )
        scope = {
            "type": "http", "method": "GET", "path": "/",
            "client": ("192.0.2.20", 1234),
            "headers": [(b"x-forwarded-for", b"198.51.100.99")],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        await middleware(scope, receive, send)
        scope["headers"] = [(b"x-forwarded-for", b"203.0.113.77")]
        messages = []
        async def capture(message):
            messages.append(message)
        await middleware(scope, receive, capture)
        self.assertEqual(messages[0]["status"], 429)
        self.assertEqual(len(calls), 1)

    async def test_non_http_scopes_pass_through_without_rate_limiting(self):
        called = []

        async def app(scope, receive, send):
            called.append(scope["type"])

        middleware = APISecurityMiddleware(app)
        await middleware({"type": "lifespan"}, None, None)
        self.assertEqual(called, ["lifespan"])


if __name__ == "__main__":
    unittest.main()
