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
        self.assertIn(b"img-src 'self' data: blob:", headers[b"content-security-policy"])
        self.assertIn(b"Yuki", sent[1]["body"])
        self.assertEqual(api.calls, 0)

    def test_web_shell_contains_provider_status_indicator(self):
        sent = self.request(YukiWebApplication(_FakeAPI()), "/")
        self.assertIn(b'id="provider-label"', sent[1]["body"])
        self.assertIn(b'id="open-annotation"', sent[1]["body"])
        self.assertIn(b'id="annotation-canvas"', sent[1]["body"])
        self.assertIn(b'id="annotation-create-project"', sent[1]["body"])
        self.assertIn(b'id="annotation-create-task"', sent[1]["body"])
        self.assertIn(b'id="annotation-upload-button"', sent[1]["body"])
        self.assertIn(b'id="annotation-undo"', sent[1]["body"])
        self.assertIn(b'id="annotation-redo"', sent[1]["body"])
        self.assertIn(b'/assets/annotation.js', sent[1]["body"])
        self.assertIn(b'/assets/annotation.css', sent[1]["body"])

    def test_chat_script_loads_authenticated_provider_status_and_oauth_callback(self):
        sent = self.request(YukiWebApplication(_FakeAPI()), "/assets/chat.js")
        self.assertIn(b"/v1/providers", sent[1]["body"])
        self.assertIn(b"loadProviderStatus", sent[1]["body"])
        self.assertIn(b'segments[2] === "callback"', sent[1]["body"])
        self.assertIn(b'segments[1] === "oauth"', sent[1]["body"])
        self.assertNotIn(b"yuki_oauth_provider", sent[1]["body"])

    def test_assets_are_allowlisted_and_served(self):
        api = _FakeAPI()
        app = YukiWebApplication(api)
        for path, expected in (("/assets/chat.js", b"sessionStorage"), ("/assets/chat.css", b"color-scheme")):
            sent = self.request(app, path)
            self.assertEqual(sent[0]["status"], 200)
            self.assertIn(expected, sent[1]["body"])
        self.assertEqual(api.calls, 0)

    def test_annotation_assets_are_allowlisted_and_served(self):
        app = YukiWebApplication(_FakeAPI())
        js = self.request(app, "/assets/annotation.js")
        css = self.request(app, "/assets/annotation.css")
        self.assertEqual(js[0]["status"], 200)
        self.assertEqual(css[0]["status"], 200)
        self.assertIn(b"/v1/annotation/tasks/", js[1]["body"])
        self.assertIn(b"human_confirmed", js[1]["body"])
        self.assertIn(b"scan_status", js[1]["body"])
        self.assertIn(b"polygon_segmentation", js[1]["body"])
        self.assertIn(b"ai_assisted: false", js[1]["body"])
        self.assertIn(b"/draft", js[1]["body"])
        self.assertIn(b"expected_revision", js[1]["body"])
        self.assertIn(b"Recovered saved draft", js[1]["body"])
        self.assertIn(b"/review-queue", js[1]["body"])
        self.assertIn(b"reviewer_user_id", js[1]["body"])
        self.assertIn(b"Grant access", self.request(app, "/")[1]["body"])
        self.assertIn(b"annotation-review-load", self.request(app, "/")[1]["body"])
        self.assertIn(b"annotation-reviewer-admin", css[1]["body"])
        self.assertEqual(self.request(app, "/assets/../api/runtime.py")[0]["status"], 404)

    def test_provider_specific_oauth_callback_path_serves_chat_shell(self):
        api = _FakeAPI()
        sent = self.request(YukiWebApplication(api), "/oauth/callback/provider-a")
        self.assertEqual(sent[0]["status"], 200)
        self.assertIn(b'id="login-view"', sent[1]["body"])
        self.assertEqual(api.calls, 0)

    def test_invalid_oauth_callback_path_is_not_served_as_shell(self):
        api = _FakeAPI()
        sent = self.request(YukiWebApplication(api), "/oauth/callback/../admin")
        self.assertEqual(sent[0]["status"], 404)
        self.assertEqual(api.calls, 1)

    def test_oauth_callback_path_rejects_non_get_methods(self):
        api = _FakeAPI()
        sent = self.request(YukiWebApplication(api), "/oauth/callback/provider-a", "POST")
        self.assertEqual(sent[0]["status"], 405)
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
