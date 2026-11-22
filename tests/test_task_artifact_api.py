import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.task_artifacts import AuthenticatedTaskArtifactAPI
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class _Auth:
    def resolve_session(self, token):
        if token == "alice-token":
            return SimpleNamespace(user_id="alice")
        return None


class _App:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})


class TaskArtifactAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = WorkflowRunStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        self.runtime = TaskRuntime(store, authorize=lambda *_: True)
        self.calls = []
        self.runtime.get_artifact = lambda **kwargs: self.calls.append(kwargs) or b"artifact-bytes"
        self.app = _App()
        self.api = AuthenticatedTaskArtifactAPI(self.app, _Auth(), self.runtime)
        self.path = "/v1/tasks/task-123/artifacts/" + ("a" * 64)

    def request(self, path=None, token="alice-token", method="GET"):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        headers = [] if token is None else [
            (b"authorization", ("Bearer " + token).encode("ascii"))
        ]
        asyncio.run(self.api({
            "type": "http", "method": method, "path": path or self.path,
            "headers": headers,
        }, receive, send))
        return sent

    def test_download_uses_session_owner_and_opaque_reference(self):
        sent = self.request()
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(sent[1]["body"], b"artifact-bytes")
        self.assertEqual(self.calls[0]["actor_id"], "alice")
        self.assertEqual(self.calls[0]["task_id"], "task-123")
        self.assertEqual(
            self.calls[0]["reference"],
            "yuki-artifact://sha256/" + ("a" * 64),
        )
        headers = dict(sent[0]["headers"])
        self.assertEqual(headers[b"cache-control"], b"private, no-store")
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")

    def test_download_requires_authentication(self):
        sent = self.request(token=None)
        self.assertEqual(sent[0]["status"], 401)
        self.assertEqual(self.calls, [])

    def test_unknown_session_is_rejected(self):
        sent = self.request(token="invalid")
        self.assertEqual(sent[0]["status"], 401)
        self.assertEqual(self.calls, [])

    def test_head_checks_artifact_but_omits_body(self):
        sent = self.request(method="HEAD")
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(sent[1]["body"], b"")
        self.assertEqual(self.calls[0]["actor_id"], "alice")

    def test_unsupported_method_is_rejected(self):
        sent = self.request(method="POST")
        self.assertEqual(sent[0]["status"], 405)
        self.assertEqual(self.calls, [])

    def test_malformed_artifact_path_does_not_reach_runtime(self):
        sent = self.request(path="/v1/tasks/task-123/artifacts/not-a-digest")
        self.assertEqual(sent[0]["status"], 404)
        self.assertEqual(self.calls, [])

    def test_other_paths_delegate_to_wrapped_application(self):
        self.request(path="/v1/auth/me")
        self.assertEqual(self.app.calls, 1)


if __name__ == "__main__":
    unittest.main()
