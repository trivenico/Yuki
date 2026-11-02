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
        asyncio.run(app({"type": "http", "method": method, "path": path}, receive, send))
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

    def test_unknown_paths_delegate_to_api_and_post_root_is_rejected(self):
        api = _FakeAPI()
        app = YukiWebApplication(api)
        self.assertEqual(self.request(app, "/v1/conversations")[0]["status"], 404)
        self.assertEqual(api.calls, 1)
        self.assertEqual(self.request(app, "/", "POST")[0]["status"], 405)


if __name__ == "__main__":
    unittest.main()
