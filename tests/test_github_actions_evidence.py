import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from core.task_evidence import TaskEvidenceStore
from integrations.github_actions_evidence import GitHubActionsEvidenceWebhook, GitHubWebhookError
from integrations.github_actions_dispatch import GitHubActionsDispatchLedger


class GitHubActionsEvidenceWebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secret = b"test-webhook-secret-that-is-long-enough"
        self.store = TaskEvidenceStore(
            str(Path(self.tmp.name) / "evidence.sqlite3"),
            attestation_verifier=lambda item: item.verifier_id == "github-actions-webhook:acme/yuki:12345",
        )
        self.current_version = 4
        self.dispatch_id = "d" * 32
        self.dispatch_ledger = GitHubActionsDispatchLedger(str(Path(self.tmp.name) / "dispatch.sqlite3"))
        self.dispatch_ledger.reserve(
            owner_id="alice", task_id="task-42", task_version=4,
            idempotency_key="e" * 64, dispatch_id=self.dispatch_id,
            repository="acme/yuki", workflow_id=12345, source_sha="a" * 40,
        )
        self.dispatch_ledger.settle("e" * 64, "dispatched")
        self.webhook = GitHubActionsEvidenceWebhook(
            webhook_secret=self.secret,
            allowed_repositories={"acme/yuki"},
            allowed_workflow_ids={12345},
            evidence_store=self.store,
            owner_for_repository={"acme/yuki": "alice"},
            task_binding_resolver=lambda owner, task: (self.current_version, "a" * 40) if owner == "alice" and task == "task-42" else None,
            dispatch_ledger=self.dispatch_ledger,
            allowed_head_branches={"main"},
        )
        self.payload = {
            "action": "completed",
            "repository": {"full_name": "acme/yuki"},
            "workflow_run": {
                "id": 987654,
                "workflow_id": 12345,
                "display_title": "[yuki-task:task-42:v4:dispatch:" + self.dispatch_id + "]",
                "name": "CI",
                "conclusion": "success",
                "head_sha": "a" * 40,
                "head_branch": "main",
                "head_repository": {"full_name": "acme/yuki"},
                "html_url": "https://github.com/acme/yuki/actions/runs/987654",
            },
        }

    def send(self, payload=None, signature=None, event="workflow_run"):
        body = json.dumps(payload if payload is not None else self.payload, separators=(",", ":")).encode()
        signature = signature or "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return self.webhook.handle(
            raw_body=body, signature_256=signature, event_name=event,
            delivery_id="12345678-1234-1234-1234-123456789abc",
        )

    def test_verified_success_is_recorded_for_exact_task_version(self):
        result = self.send()
        self.assertTrue(result.accepted)
        self.assertEqual(result.task_id, "task-42")
        self.assertEqual(result.task_version, 4)
        records = self.store.list_verified(owner_id="alice", task_id="task-42", task_version=4)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "ci_workflow")

    def test_duplicate_delivery_is_idempotent(self):
        first = self.send()
        second = self.send()
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(len(self.store.list_verified(owner_id="alice", task_id="task-42")), 1)

    def test_invalid_signature_and_non_workflow_event_rejected(self):
        with self.assertRaises(GitHubWebhookError):
            self.send(signature="sha256=" + "0" * 64)
        with self.assertRaises(GitHubWebhookError):
            self.send(event="push")

    def test_unallowlisted_repo_workflow_or_branch_rejected(self):
        for field, value in (
            ("repository", {"full_name": "attacker/repo"}),
            ("workflow_id", 999),
            ("head_branch", "untrusted-branch"),
        ):
            payload = json.loads(json.dumps(self.payload))
            if field == "repository":
                payload["repository"] = value
            else:
                payload["workflow_run"][field] = value
            with self.assertRaises(GitHubWebhookError):
                self.send(payload)

    def test_fork_run_and_stale_task_version_rejected(self):
        payload = json.loads(json.dumps(self.payload))
        payload["workflow_run"]["head_repository"]["full_name"] = "fork/yuki"
        with self.assertRaises(GitHubWebhookError):
            self.send(payload)
        self.current_version = 5
        with self.assertRaises(GitHubWebhookError):
            self.send()
        self.current_version = 4
        payload = json.loads(json.dumps(self.payload))
        payload["workflow_run"]["head_sha"] = "c" * 40
        with self.assertRaises(GitHubWebhookError):
            self.send(payload)

    def test_unsuccessful_workflow_never_creates_evidence(self):
        payload = json.loads(json.dumps(self.payload))
        payload["workflow_run"]["conclusion"] = "failure"
        result = self.send(payload)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "workflow_not_successful")
        self.assertEqual(self.store.list_verified(owner_id="alice", task_id="task-42"), ())

    def test_one_dispatch_cannot_be_reused_by_a_second_run(self):
        first = self.send()
        self.assertTrue(first.accepted)
        payload = json.loads(json.dumps(self.payload))
        payload["workflow_run"]["id"] = 987655
        payload["workflow_run"]["html_url"] = "https://github.com/acme/yuki/actions/runs/987655"
        with self.assertRaises(GitHubWebhookError):
            self.send(payload)

    def test_unregistered_dispatch_cannot_create_evidence(self):
        payload = json.loads(json.dumps(self.payload))
        payload["workflow_run"]["display_title"] = "[yuki-task:task-42:v4:dispatch:" + ("f" * 32) + "]"
        with self.assertRaises(GitHubWebhookError):
            self.send(payload)

    def test_requires_correlation_token_and_matching_run_url(self):
        payload = json.loads(json.dumps(self.payload))
        payload["workflow_run"]["display_title"] = "ordinary workflow"
        with self.assertRaises(GitHubWebhookError):
            self.send(payload)
        payload = json.loads(json.dumps(self.payload))
        payload["workflow_run"]["html_url"] = "https://github.com/attacker/repo/actions/runs/987654"
        with self.assertRaises(GitHubWebhookError):
            self.send(payload)


if __name__ == "__main__":
    unittest.main()
