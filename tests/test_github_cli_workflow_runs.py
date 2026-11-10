import json
import subprocess
import unittest
from unittest.mock import patch

from automation.github_cli_workflow_runs import GitHubCLIWorkflowRunClient


class GitHubCLIWorkflowRunClientTests(unittest.TestCase):
    def setUp(self):
        self.client = GitHubCLIWorkflowRunClient("owner/repo")
        self.sha = "a" * 40

    @patch("automation.github_cli_workflow_runs.shutil.which", return_value="/usr/bin/gh")
    @patch("automation.github_cli_workflow_runs.subprocess.run")
    def test_fetches_paginated_json_lines_without_shell(self, run, which):
        runs = [{"id": 1, "head_sha": self.sha}, {"id": 2, "head_sha": self.sha}]
        run.return_value = subprocess.CompletedProcess(
            args=["gh"], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in runs) + "\n").encode(),
            stderr=b"",
        )
        self.assertEqual(self.client(self.sha), runs)
        args = run.call_args.args[0]
        self.assertEqual(args[0:3], ["gh", "api", "--paginate"])
        self.assertIn(self.sha, args[-1])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    @patch("automation.github_cli_workflow_runs.shutil.which", return_value=None)
    def test_missing_gh_cli_fails(self, which):
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            self.client(self.sha)

    @patch("automation.github_cli_workflow_runs.shutil.which", return_value="/usr/bin/gh")
    @patch("automation.github_cli_workflow_runs.subprocess.run")
    def test_nonzero_exit_fails_without_exposing_stderr(self, run, which):
        run.return_value = subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout=b"", stderr=b"secret token leaked"
        )
        with self.assertRaisesRegex(RuntimeError, "lookup failed") as ctx:
            self.client(self.sha)
        self.assertNotIn("secret token", str(ctx.exception))

    @patch("automation.github_cli_workflow_runs.shutil.which", return_value="/usr/bin/gh")
    @patch("automation.github_cli_workflow_runs.subprocess.run")
    def test_timeout_fails_closed(self, run, which):
        run.side_effect = subprocess.TimeoutExpired(["gh"], 30)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.client(self.sha)

    @patch("automation.github_cli_workflow_runs.shutil.which", return_value="/usr/bin/gh")
    @patch("automation.github_cli_workflow_runs.subprocess.run")
    def test_malformed_json_fails_closed(self, run, which):
        run.return_value = subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout=b"{bad json}\n", stderr=b""
        )
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            self.client(self.sha)

    @patch("automation.github_cli_workflow_runs.shutil.which", return_value="/usr/bin/gh")
    @patch("automation.github_cli_workflow_runs.subprocess.run")
    def test_oversized_response_fails(self, run, which):
        client = GitHubCLIWorkflowRunClient("owner/repo", max_output_bytes=1024)
        run.return_value = subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout=b"x" * 1025, stderr=b""
        )
        with self.assertRaisesRegex(RuntimeError, "size limit"):
            client(self.sha)

    def test_invalid_repository_and_sha_are_rejected(self):
        with self.assertRaises(ValueError):
            GitHubCLIWorkflowRunClient("../owner/repo")
        with self.assertRaises(ValueError):
            self.client("bad-sha")


if __name__ == "__main__":
    unittest.main()
