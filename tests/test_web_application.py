import unittest

from api.web import YukiWebApplication


class _FakeAPI:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})


class YukiWebApplicationTests(unittest.TestCase):
    def request(self, app, path, method="GET"):
        import asyncio
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        headers = [(b"host", b"localhost:8000")]
        asyncio.run(app({
            "type": "http", "method": method, "path": path, "headers": headers,
        }, receive, send))
        return sent

    def test_root_serves_chat_shell_with_security_headers(self):
        api = _FakeAPI()
        sent = self.request(YukiWebApplication(api), "/")
        self.assertEqual(sent[0]["status"], 200)
        headers = dict(sent[0]["headers"])
        self.assertEqual(headers[b"content-type"], b"text/html; charset=utf-8")
        self.assertEqual(headers[b"x-frame-options"], b"DENY")
        self.assertIn(b"script-src 'self'", headers[b"content-security-policy"])
        self.assertIn(b"Yuki", sent[1]["body"])
        self.assertEqual(api.calls, 0)

    def test_web_shell_contains_provider_status_indicator(self):
        sent = self.request(YukiWebApplication(_FakeAPI()), "/")
        self.assertIn(b'id="provider-label"', sent[1]["body"])

    def test_chat_script_loads_authenticated_provider_status(self):
        sent = self.request(YukiWebApplication(_FakeAPI()), "/assets/chat.js")
        self.assertIn(b"/v1/providers", sent[1]["body"])
        self.assertIn(b"loadProviderStatus", sent[1]["body"])

    def test_chat_shell_exposes_owner_private_memory_controls(self):
        html = self.request(YukiWebApplication(_FakeAPI()), "/")[1]["body"]
        self.assertIn(b'id="open-memory"', html)
        self.assertIn(b'id="memory-panel"', html)
        script = self.request(YukiWebApplication(_FakeAPI()), "/assets/chat.js")[1]["body"]
        for marker in (b"/v1/memory", b"loadMemory", b"Approve", b"Reject", b"Correct", b"Retire", b"Delete"):
            self.assertIn(marker, script)
        style = self.request(YukiWebApplication(_FakeAPI()), "/assets/chat.css")[1]["body"]
        self.assertIn(b".memory-panel", style)

    def test_chat_shell_exposes_owner_approved_memory_suggestions(self):
        app = YukiWebApplication(_FakeAPI())
        html = self.request(app, "/")[1]["body"]
        self.assertIn(b'id="memory-candidate-list"', html)
        script = self.request(app, "/assets/chat.js")[1]["body"]
        for marker in (
            b"/v1/memory/candidates", b"Approve and remember",
            b"requires_owner_approval", b"conversation_id",
        ):
            self.assertIn(marker, script)
        style = self.request(app, "/assets/chat.css")[1]["body"]
        self.assertIn(b".memory-candidates", style)

    def test_web_memory_panel_exposes_typed_owner_profile_preferences(self):
        app = YukiWebApplication(_FakeAPI())
        html = self.request(app, "/")[1]["body"]
        for marker in (
            b'id="profile-response-style"', b'id="profile-response-tone"',
            b'id="profile-explanation-level"', b'id="save-profile"',
        ):
            self.assertIn(marker, html)
        script = self.request(app, "/assets/chat.js")[1]["body"]
        self.assertIn(b"/v1/profile/preferences", script)
        self.assertIn(b"owner_web_preference", script)
        style = self.request(app, "/assets/chat.css")[1]["body"]
        self.assertIn(b".profile-preferences", style)

    def test_assets_are_allowlisted_and_served(self):
        api = _FakeAPI()
        app = YukiWebApplication(api)
        for path, expected in (("/assets/chat.js", b"sessionStorage"), ("/assets/chat.css", b"color-scheme")):
            sent = self.request(app, path)
            self.assertEqual(sent[0]["status"], 200)
            self.assertIn(expected, sent[1]["body"])
        self.assertEqual(api.calls, 0)

    def test_health_endpoint_is_public_and_minimal(self):
        sent = self.request(YukiWebApplication(_FakeAPI()), "/healthz")
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(sent[1]["body"], b'{"status": "ok", "service": "yuki"}')


    def test_rejects_missing_duplicate_and_untrusted_host_headers(self):
        import asyncio
        async def invoke(headers):
            sent = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                sent.append(message)
            await YukiWebApplication(_FakeAPI())(
                {"type": "http", "method": "GET", "path": "/healthz", "headers": headers},
                receive, send,
            )
            return sent[0]["status"]
        self.assertEqual(asyncio.run(invoke([])), 400)
        self.assertEqual(asyncio.run(invoke([
            (b"host", b"localhost:8000"), (b"host", b"localhost:8000"),
        ])), 400)
        self.assertEqual(asyncio.run(invoke([(b"host", b"attacker.example")])), 421)

    def test_rejects_cross_origin_and_cross_site_state_changes(self):
        import asyncio
        async def invoke(headers):
            api = _FakeAPI()
            sent = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                sent.append(message)
            await YukiWebApplication(api)(
                {"type": "http", "method": "POST", "path": "/v1/auth/login",
                 "headers": [(b"host", b"localhost:8000"), *headers]},
                receive, send,
            )
            return sent[0]["status"], api.calls
        self.assertEqual(asyncio.run(invoke([
            (b"origin", b"https://evil.example"),
        ])), (403, 0))
        self.assertEqual(asyncio.run(invoke([
            (b"sec-fetch-site", b"cross-site"),
        ])), (403, 0))
        self.assertEqual(asyncio.run(invoke([
            (b"origin", b"http://localhost:9000"),
        ])), (403, 0))

    def test_allows_same_origin_and_non_browser_bearer_clients(self):
        import asyncio
        async def invoke(headers):
            api = _FakeAPI()
            sent = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                sent.append(message)
            await YukiWebApplication(api)(
                {"type": "http", "method": "POST", "path": "/v1/auth/login",
                 "headers": [(b"host", b"localhost:8000"), *headers]},
                receive, send,
            )
            return sent[0]["status"], api.calls
        self.assertEqual(asyncio.run(invoke([
            (b"origin", b"http://localhost:8000"),
        ])), (404, 1))
        self.assertEqual(asyncio.run(invoke([
            (b"authorization", b"Bearer client-token"),
        ])), (404, 1))

    def test_allowed_hosts_are_explicit_and_wildcards_are_rejected(self):
        with self.assertRaises(ValueError):
            YukiWebApplication(_FakeAPI(), allowed_hosts=["*"])
        app = YukiWebApplication(_FakeAPI(), allowed_hosts=["chat.example.com"])
        self.assertEqual(app.allowed_hosts, frozenset({"chat.example.com"}))

    def test_unknown_paths_delegate_to_api_and_post_root_is_rejected(self):
        api = _FakeAPI()
        app = YukiWebApplication(api)
        self.assertEqual(self.request(app, "/v1/conversations")[0]["status"], 404)
        self.assertEqual(api.calls, 1)
        self.assertEqual(self.request(app, "/", "POST")[0]["status"], 405)


if __name__ == "__main__":
    unittest.main()
