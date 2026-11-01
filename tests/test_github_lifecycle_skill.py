import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from skills.github_lifecycle import GitHubLifecycleSkill
from skills.base import Skill, SkillResult

class GitHubLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.skill = GitHubLifecycleSkill(allowed_roots=[self.root])

    def tearDown(self):
        self.temp.cleanup()

    @patch("skills.github_lifecycle.shutil.which", return_value="/usr/bin/gh")
    @patch("skills.github_lifecycle.GitHubLifecycleSkill._run")
    def test_repo_creation_requires_exact_confirmation(self, run, which):
        with patch.object(self.skill, "_repo", return_value=self.repo), patch.object(self.skill, "_git", return_value=""):
            bad = self.skill.run(json.dumps({"action":"create_repo","repo_path":str(self.repo),
                "repository":"acme/demo","visibility":"private","confirm":"yes"}))
        self.assertFalse(bad.ok)
        run.assert_not_called()

    @patch("skills.github_lifecycle.shutil.which", return_value="/usr/bin/gh")
    @patch("skills.github_lifecycle.GitHubLifecycleSkill._run", return_value="https://github.com/acme/demo/pull/7")
    def test_pr_creation_requires_exact_confirmation(self, run, which):
        with patch.object(self.skill, "_repo", return_value=self.repo), patch.object(self.skill, "_slug_from_repo", return_value="acme/demo"), patch.object(self.skill, "_git", return_value="feature"):
            bad = self.skill.run(json.dumps({"action":"create_pr","repo_path":str(self.repo),
                "title":"title","base":"main","confirm":"yes"}))
        self.assertFalse(bad.ok)
        run.assert_not_called()

    @patch("skills.github_lifecycle.shutil.which", return_value="/usr/bin/gh")
    @patch("skills.github_lifecycle.GitHubLifecycleSkill._run")
    def test_commit_ci_evidence_is_bound_to_exact_sha(self, run, which):
        sha = "a" * 40
        run.return_value = json.dumps([{
            "databaseId": 123, "name": "Quality gates", "status": "completed",
            "conclusion": "success", "headSha": sha, "event": "pull_request",
            "url": "https://github.com/acme/demo/actions/runs/123",
            "createdAt": "2026-11-01T00:00:00Z", "updatedAt": "2026-11-01T00:01:00Z",
        }])
        with patch.object(self.skill, "_repo", return_value=self.repo), \
             patch.object(self.skill, "_slug_from_repo", return_value="acme/demo"):
            result = self.skill.run(json.dumps({
                "action": "commit_ci_evidence", "repo_path": str(self.repo),
                "commit_sha": sha, "limit": 10,
            }))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["commit_sha"], sha)
        self.assertTrue(result.data["all_returned_runs_successful"])
        args = run.call_args.args[0]
        self.assertIn("--commit", args)
        self.assertIn(sha, args)

    @patch("skills.github_lifecycle.shutil.which", return_value="/usr/bin/gh")
    @patch("skills.github_lifecycle.GitHubLifecycleSkill._run")
    def test_commit_ci_evidence_rejects_mismatched_sha(self, run, which):
        requested = "a" * 40
        run.return_value = json.dumps([{
            "databaseId": 123, "name": "Wrong run", "status": "completed",
            "conclusion": "success", "headSha": "b" * 40,
        }])
        with patch.object(self.skill, "_repo", return_value=self.repo), \
             patch.object(self.skill, "_slug_from_repo", return_value="acme/demo"):
            result = self.skill.run(json.dumps({
                "action": "commit_ci_evidence", "repo_path": str(self.repo),
                "commit_sha": requested,
            }))
        self.assertFalse(result.ok)

    def test_commit_ci_evidence_rejects_abbreviated_sha(self):
        result = self.skill.run(json.dumps({
            "action": "commit_ci_evidence", "repo_path": str(self.repo),
            "commit_sha": "abc123",
        }))
        self.assertFalse(result.ok)

    def test_lifecycle_skill_can_accept_bounded_long_pr_body(self):
        self.assertEqual(self.skill.max_argument_length, 12000)
        self.assertEqual(Skill.max_argument_length if hasattr(Skill, "max_argument_length") else 500, 500)

    def test_rejects_unconfigured_path(self):
        result = self.skill.run(json.dumps({"action":"list_issues","repo_path":"/tmp"}))
        self.assertFalse(result.ok)

    def test_unknown_action_fails_closed(self):
        result = self.skill.run(json.dumps({"action":"shell","cmd":"rm -rf /"}))
        self.assertFalse(result.ok)

if __name__ == "__main__":
    unittest.main()
