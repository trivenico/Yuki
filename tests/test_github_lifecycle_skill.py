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
