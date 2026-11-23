import json
import unittest

from integrations.github_app_actions_read import GitHubAppActionsReadClient, GitHubAppReadError


class GitHubAppActionsReadClientTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.token_calls = []
        self.responses = []

        def transport(**kwargs):
            self.calls.append(kwargs)
            return self.responses.pop(0)

        self.client = GitHubAppActionsReadClient(
            repository="acme/yuki",
            installation_token_for_repository=lambda repo: self.token_calls.append(repo) or "app-install-token",
            http_get=transport,
        )

    @staticmethod
    def response(value, status=200, headers=None):
        return {"status": status, "headers": headers or {},
                "body": value if isinstance(value, bytes) else json.dumps(value).encode()}

    def test_run_lookup_is_pinned_and_normalizes_metadata(self):
        self.responses.append(self.response({
            "id": 7, "workflow_id": 88, "conclusion": "success",
            "head_sha": "a" * 40, "head_branch": "main",
            "repository": {"full_name": "acme/yuki"},
        }))
        run = self.client.workflow_run("acme/yuki", 7)
        self.assertEqual(run["repository_full_name"], "acme/yuki")
        self.assertEqual(self.calls[0]["url"], "https://api.github.com/repos/acme/yuki/actions/runs/7")
        self.assertEqual(self.calls[0]["headers"]["Authorization"], "Bearer app-install-token")
        self.assertFalse(self.calls[0]["follow_redirects"])

    def test_artifact_lookup_requires_one_exact_name(self):
        self.responses.append(self.response({"artifacts": [{
            "id": 9, "name": "yuki-test-report-7", "expired": False,
            "size_in_bytes": 123, "digest": "sha256:" + "b" * 64,
            "workflow_run": {"id": 7},
        }]}))
        item = self.client.artifact("acme/yuki", 7, "yuki-test-report-7")
        self.assertEqual(item["workflow_run_id"], 7)
        self.assertEqual(item["digest"], "sha256:" + "b" * 64)
        self.assertIn("/actions/runs/7/artifacts?per_page=100", self.calls[0]["url"])

    def test_artifact_redirect_does_not_forward_installation_token(self):
        archive = b"zip bytes"
        self.responses.append(self.response(b"", status=302, headers={
            "Location": "https://signed.blob.core.windows.net/container/archive?sig=secret",
        }))
        self.responses.append(self.response(archive))
        result = self.client.download_artifact("acme/yuki", 9)
        self.assertEqual(result, archive)
        self.assertIn("Authorization", self.calls[0]["headers"])
        self.assertNotIn("Authorization", self.calls[1]["headers"])
        self.assertEqual(self.calls[1]["headers"]["Accept"], "application/octet-stream")
        self.assertEqual(len(self.token_calls), 1)

    def test_untrusted_redirect_host_is_rejected(self):
        self.responses.append(self.response(b"", status=302, headers={
            "Location": "https://attacker.example/archive",
        }))
        with self.assertRaises(GitHubAppReadError):
            self.client.download_artifact("acme/yuki", 9)
        self.assertEqual(len(self.calls), 1)

    def test_repository_and_artifact_name_are_validated(self):
        with self.assertRaises(GitHubAppReadError):
            self.client.workflow_run("attacker/repo", 1)
        with self.assertRaises(ValueError):
            self.client.artifact("acme/yuki", 1, "../secret")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
