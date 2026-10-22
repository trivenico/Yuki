import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.github_push import GitHubPushSkill


class GitHubPushSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.skill = GitHubPushSkill(allowed_roots=[self.root])

    def tearDown(self):
        self.temp.cleanup()

    def test_remote_validation_accepts_only_github(self):
        self.assertEqual(self.skill._remote_slug("https://github.com/acme/demo.git"), "acme/demo")
        self.assertEqual(self.skill._remote_slug("git@github.com:acme/demo.git"), "acme/demo")
        for remote in ("https://evil.example/acme/demo", "https://token@github.com/acme/demo",
                       "https://github.com/acme/demo?token=secret", "file:///tmp/repo"):
            with self.assertRaises(ValueError):
                self.skill._remote_slug(remote)

    def test_rejects_paths_outside_configured_root(self):
        outside = Path(tempfile.gettempdir()).resolve()
        with self.assertRaises(ValueError):
            self.skill._resolve_repo(str(outside))

    def test_status_does_not_push_and_reports_confirmation(self):
        outputs = {
            ("rev-parse", "--show-toplevel"): str(self.repo.resolve()),
            ("branch", "--show-current"): "main",
            ("remote", "get-url", "origin"): "https://github.com/acme/demo.git",
            ("status", "--porcelain", "--untracked-files=normal"): "",
        }
        with patch.object(self.skill, "_run_git", side_effect=lambda p, *a: outputs[tuple(a)]):
            result = self.skill.run(json.dumps({"action": "status", "repo_path": str(self.repo.resolve())}))
        self.assertTrue(result.ok)
        self.assertTrue(result.data["confirmation_required"])
        self.assertTrue(result.data["working_tree_clean"])

    def test_push_requires_exact_confirmation_and_clean_tree(self):
        outputs = {
            ("rev-parse", "--show-toplevel"): str(self.repo.resolve()),
            ("branch", "--show-current"): "main",
            ("remote", "get-url", "origin"): "https://github.com/acme/demo.git",
            ("status", "--porcelain", "--untracked-files=normal"): "",
        }
        calls = []
        def git(path, *args):
            calls.append(args)
            if tuple(args) == ("rev-parse", "--verify", "HEAD"):
                return "deadbeef"
            if tuple(args) == ("rev-parse", "--short", "HEAD"):
                return "deadbeef"
            if tuple(args) == ("push", "--porcelain", "origin", "HEAD:refs/heads/main"):
                return ""
            return outputs[tuple(args)]
        with patch.object(self.skill, "_run_git", side_effect=git):
            bad = self.skill.run(json.dumps({"action": "push", "repo_path": str(self.repo.resolve()), "confirm": "yes"}))
        self.assertFalse(bad.ok)
        self.assertFalse(any(c[0] == "push" for c in calls))

        calls.clear()
        with patch.object(self.skill, "_run_git", side_effect=git):
            good = self.skill.run(json.dumps({"action": "push", "repo_path": str(self.repo.resolve()),
                                               "confirm": "PUSH acme/demo main"}))
        self.assertTrue(good.ok)
        self.assertTrue(good.data["pushed"])
        self.assertIn(("push", "--porcelain", "origin", "HEAD:refs/heads/main"), calls)

    def test_dirty_tree_blocks_push(self):
        outputs = {
            ("rev-parse", "--show-toplevel"): str(self.repo.resolve()),
            ("branch", "--show-current"): "main",
            ("remote", "get-url", "origin"): "https://github.com/acme/demo.git",
            ("status", "--porcelain", "--untracked-files=normal"): " M app.py",
        }
        with patch.object(self.skill, "_run_git", side_effect=lambda p, *a: outputs[tuple(a)]) as git:
            result = self.skill.run(json.dumps({"action": "push", "repo_path": str(self.repo.resolve()),
                                                  "confirm": "PUSH acme/demo main"}))
        self.assertFalse(result.ok)
        self.assertIn("not clean", result.message)


if __name__ == "__main__":
    unittest.main()
