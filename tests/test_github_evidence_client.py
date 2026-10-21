import io
import json
import unittest
from unittest.mock import patch

from core.github_evidence_client import GitHubEvidenceClient, GitHubEvidenceError
from core.github_check_gate import evaluate_pull_request_checks

SHA = "a" * 40


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit=-1):
        return self.payload[:limit]


def pr_payload():
    return {
        "state": "open", "draft": False,
        "head": {"sha": SHA}, "base": {"ref": "main"},
    }


def checks_payload(conclusion="success"):
    return {"total_count": 1, "check_runs": [{
        "name": "unit", "status": "completed",
        "conclusion": conclusion, "head_sha": SHA,
    }]}


class GitHubEvidenceClientTests(unittest.TestCase):
    def setUp(self):
        self.client = GitHubEvidenceClient("test-token")

    @patch("urllib.request.OpenerDirector.open")
    def test_fetches_current_pr_and_check_evidence(self, mocked_open):
        mocked_open.side_effect = [FakeResponse(pr_payload()), FakeResponse(checks_payload())]
        evidence = self.client.fetch_pull_request_evidence(
            "trivenico", "Yuki", 72, required_checks=("unit",)
        )
        result = evaluate_pull_request_checks(evidence, expected_head_sha=SHA)
        self.assertEqual(result.decision, "ready-for-required-checks")
        self.assertEqual(mocked_open.call_count, 2)
        self.assertTrue(mocked_open.call_args_list[0].args[0].full_url.endswith("/pulls/72"))

    @patch("urllib.request.OpenerDirector.open")
    def test_failed_check_blocks(self, mocked_open):
        mocked_open.side_effect = [FakeResponse(pr_payload()), FakeResponse(checks_payload("failure"))]
        evidence = self.client.fetch_pull_request_evidence(
            "trivenico", "Yuki", 72, required_checks=("unit",)
        )
        self.assertEqual(evaluate_pull_request_checks(evidence, expected_head_sha=SHA).decision, "block")

    def test_rejects_bad_repository_path_and_pr_number(self):
        with self.assertRaises(ValueError):
            self.client.fetch_pull_request_evidence("..", "Yuki", 1, required_checks=())
        with self.assertRaises(ValueError):
            self.client.fetch_pull_request_evidence("trivenico", "Yuki", True, required_checks=())

    def test_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            GitHubEvidenceClient("", timeout=5)
        with self.assertRaises(ValueError):
            GitHubEvidenceClient("token", timeout=0)

    @patch("urllib.request.OpenerDirector.open")
    def test_invalid_api_shape_fails_closed(self, mocked_open):
        mocked_open.return_value = FakeResponse([])
        with self.assertRaises(GitHubEvidenceError):
            self.client.fetch_pull_request_evidence(
                "trivenico", "Yuki", 72, required_checks=("unit",)
            )


if __name__ == "__main__":
    unittest.main()
