import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.yuki_evolution_worktree import (
    EvolutionError,
    discard_evolution,
    start_evolution,
)


class YukiEvolutionWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.env = os.environ.copy()
        self.env.update({
            "GIT_AUTHOR_NAME": "Yuki Tests",
            "GIT_AUTHOR_EMAIL": "yuki-tests@example.invalid",
            "GIT_COMMITTER_NAME": "Yuki Tests",
            "GIT_COMMITTER_EMAIL": "yuki-tests@example.invalid",
        })
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Yuki Tests")
        self.git("config", "user.email", "yuki-tests@example.invalid")
        (self.root / "README.md").write_text("baseline\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "initial commit")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True, capture_output=True, text=True, env=self.env,
        ).stdout.strip()

    def test_start_creates_checkpoint_and_isolated_worktree(self):
        state = start_evolution(self.root)
        worktree = Path(state["worktree"])
        self.assertTrue(worktree.is_dir())
        self.assertEqual(self.git("rev-parse", state["checkpoint_tag"] + "^{commit}"), state["base_commit"])
        self.assertEqual(
            subprocess.run(["git", "-C", str(worktree), "branch", "--show-current"],
                           check=True, capture_output=True, text=True).stdout.strip(),
            state["branch"],
        )
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "baseline\n")

    def test_start_rejects_worktree_root_inside_source_checkout(self):
        with self.assertRaisesRegex(EvolutionError, "outside the source repository"):
            start_evolution(self.root, self.root / "nested-worktrees")
        self.assertFalse((self.root / "nested-worktrees").exists())

    def test_start_rejects_dirty_source_repository(self):
        (self.root / "README.md").write_text("uncommitted\n", encoding="utf-8")
        with self.assertRaisesRegex(EvolutionError, "working tree must be clean"):
            start_evolution(self.root)

    def test_discard_refuses_dirty_worktree_unless_force_is_explicit(self):
        state = start_evolution(self.root)
        worktree = Path(state["worktree"])
        (worktree / "README.md").write_text("agent changes\n", encoding="utf-8")
        with self.assertRaisesRegex(EvolutionError, "uncommitted changes"):
            discard_evolution(self.root, Path(state["worktree"]).name.removeprefix(self.root.name + "-"))
        identifier = state["branch"].removeprefix("yuki/evolution/")
        result = discard_evolution(self.root, identifier, force=True)
        self.assertTrue(result["discarded"])
        self.assertFalse(worktree.exists())
        self.assertEqual(self.git("rev-parse", state["checkpoint_tag"] + "^{commit}"), state["base_commit"])

    def test_discard_preserves_branch_when_it_contains_commits(self):
        state = start_evolution(self.root)
        worktree = Path(state["worktree"])
        (worktree / "agent-change.txt").write_text("review me\\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(worktree), "add", "agent-change.txt"],
                       check=True, capture_output=True, text=True, env=self.env)
        subprocess.run(["git", "-C", str(worktree), "commit", "-m", "agent change"],
                       check=True, capture_output=True, text=True, env=self.env)
        committed_tip = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, env=self.env,
        ).stdout.strip()

        identifier = state["branch"].removeprefix("yuki/evolution/")
        result = discard_evolution(self.root, identifier)
        self.assertTrue(result["branch_preserved"])
        self.assertFalse(worktree.exists())
        self.assertEqual(self.git("rev-parse", state["branch"]), committed_tip)

    def test_tampered_state_cannot_authorize_deleting_another_branch(self):
        state = start_evolution(self.root)
        identifier = state["branch"].removeprefix("yuki/evolution/")
        common_dir = Path(self.git("rev-parse", "--git-common-dir"))
        state_path = (self.root / common_dir / "yuki-evolution" / (identifier + ".json"))
        metadata = json.loads(state_path.read_text(encoding="utf-8"))
        metadata["branch"] = "yuki/evolution/20260101T000000Z-deadbeef"
        state_path.write_text(json.dumps(metadata), encoding="utf-8")

        with self.assertRaisesRegex(EvolutionError, "unexpected branch"):
            discard_evolution(self.root, identifier)
        self.assertTrue(Path(state["worktree"]).is_dir())
        self.assertEqual(self.git("branch", "--list", state["branch"]), state["branch"])

    def test_invalid_identifier_is_rejected(self):
        with self.assertRaisesRegex(EvolutionError, "invalid evolution identifier"):
            discard_evolution(self.root, "../main")


if __name__ == "__main__":
    unittest.main()
