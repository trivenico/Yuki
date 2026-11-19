import asyncio
import json
import unittest

from api.web import YukiWebApplication


class WebReadinessTests(unittest.TestCase):
    def test_readiness_returns_service_unavailable_until_runtime_is_ready(self):
        state = {"ready": False}
        app = YukiWebApplication(object(), readiness=lambda: state["ready"])

        async def request():
            sent = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                sent.append(message)
            await app(
                {"type": "http", "method": "GET", "path": "/readyz", "headers": []},
                receive, send,
            )
            return sent

        before = asyncio.run(request())
        self.assertEqual(before[0]["status"], 503)
        self.assertEqual(json.loads(before[1]["body"]), {
            "status": "not_ready", "service": "yuki",
        })

        state["ready"] = True
        after = asyncio.run(request())
        self.assertEqual(after[0]["status"], 200)
        self.assertEqual(json.loads(after[1]["body"]), {
            "status": "ready", "service": "yuki",
        })

    def test_liveness_remains_independent_of_startup_readiness(self):
        app = YukiWebApplication(object(), readiness=lambda: False)
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        async def invoke():
            await app(
                {"type": "http", "method": "GET", "path": "/healthz", "headers": []},
                receive, send,
            )
        asyncio.run(invoke())
        self.assertEqual(sent[0]["status"], 200)


if __name__ == "__main__":
    unittest.main()