import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from api.github_actions_evidence import GitHubActionsEvidenceAPI
from core.task_evidence import TaskEvidenceStore
from integrations.github_actions_evidence import GitHubActionsEvidenceWebhook


class GitHubActionsEvidenceAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secret = b"api-test-webhook-secret-long-enough"
        store = TaskEvidenceStore(
            str(Path(self.tmp.name) / "evidence.sqlite3"),
            attestation_verifier=lambda item: item.verifier_id == "github-actions-webhook:acme/yuki:12345",
        )
        webhook = GitHubActionsEvidenceWebhook(
            webhook_secret=self.secret, allowed_repositories={"acme/yuki"},
            allowed_workflow_ids={12345}, evidence_store=store,
            owner_for_repository={"acme/yuki": "alice"},
            task_binding_resolver=lambda owner, task: (2, "b" * 40) if (owner, task) == ("alice", "task-9") else None,
            allowed_head_branches={"main"},
        )
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        self.store = store
        self.api = GitHubActionsEvidenceAPI(app, webhook)

    async def request(self, body, *, signature=None, event="workflow_run", method="POST", path="/webhooks/github/actions"):
        headers = [
            (b"x-github-event", event.encode()),
            (b"x-github-delivery", b"12345678-1234-1234-1234-123456789abc"),
            (b"x-hub-signature-256", (signature or "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()).encode()),
        ]
        sent = []
        delivered = False
        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        async def send(message):
            sent.append(message)
        await self.api({"type": "http", "method": method, "path": path, "headers": headers}, receive, send)
        return sent

    def payload(self, conclusion="success"):
        return json.dumps({
            "action": "completed",
            "repository": {"full_name": "acme/yuki"},
            "workflow_run": {
                "id": 44, "workflow_id": 12345,
                "display_title": "[yuki-task:task-9:v2]",
                "conclusion": conclusion, "head_sha": "b" * 40,
                "head_branch": "main", "head_repository": {"full_name": "acme/yuki"},
                "html_url": "https://github.com/acme/yuki/actions/runs/44",
            },
        }, separators=(",", ":")).encode()

    async def test_valid_webhook_returns_accepted_and_records_evidence(self):
        sent = await self.request(self.payload())
        self.assertEqual(sent[0]["status"], 202)
        result = json.loads(sent[1]["body"])
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.store.list_verified(owner_id="alice", task_id="task-9", task_version=2)), 1)

    async def test_invalid_signature_is_rejected_without_record(self):
        sent = await self.request(self.payload(), signature="sha256=" + "0" * 64)
        self.assertEqual(sent[0]["status"], 401)
        self.assertEqual(self.store.list_verified(owner_id="alice", task_id="task-9"), ())

    async def test_non_success_is_acknowledged_but_not_recorded(self):
        sent = await self.request(self.payload("failure"))
        self.assertEqual(sent[0]["status"], 200)
        self.assertFalse(json.loads(sent[1]["body"])["accepted"])
        self.assertEqual(self.store.list_verified(owner_id="alice", task_id="task-9"), ())

    async def test_body_limit_and_method_guard(self):
        too_large = await self.request(b"x" * 1_000_001)
        self.assertEqual(too_large[0]["status"], 413)
        wrong_method = await self.request(b"", method="GET")
        self.assertEqual(wrong_method[0]["status"], 405)

    async def test_unrelated_routes_pass_through(self):
        sent = await self.request(b"", path="/healthz")
        self.assertEqual(sent[0]["status"], 204)


if __name__ == "__main__":
    unittest.main()
