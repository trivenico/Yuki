import os
from pathlib import Path
import tempfile
import unittest

from core.builtin_manifest_skills import register_builtin_workspace_list
from core.skill_dispatch import SkillDispatcher


class BuiltinWorkspaceListTests(unittest.TestCase):
    def _dispatcher(self, workspace):
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"})
        register_builtin_workspace_list(dispatcher, workspace)
        return dispatcher

    def test_lists_names_only_and_hides_hidden_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.txt").write_text("do not read this secret content", encoding="utf-8")
            (root / "folder").mkdir()
            (root / ".hidden").write_text("private", encoding="utf-8")
            result = self._dispatcher(root).dispatch("workspace.list", {"path": ".", "limit": 25})
            self.assertTrue(result.ok, result.code)
            self.assertEqual(result.output["path"], ".")
            self.assertEqual(result.output["entries"], [
                {"name": "alpha.txt", "type": "file"},
                {"name": "folder", "type": "directory"},
            ])
            self.assertNotIn("do not read this secret content", str(result.output))
            self.assertFalse(result.output["truncated"])

    def test_rejects_parent_traversal_and_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (Path(outside) / "private.txt").write_text("secret", encoding="utf-8")
            dispatcher = self._dispatcher(root)
            for path in ("../", str(Path(outside)), "../../etc"):
                with self.subTest(path=path):
                    result = dispatcher.dispatch("workspace.list", {"path": path, "limit": 10})
                    self.assertFalse(result.ok)
                    self.assertNotIn("secret", str(result))

    def test_rejects_invalid_limits_and_extra_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = self._dispatcher(tmp)
            self.assertEqual(dispatcher.dispatch("workspace.list", {"path": ".", "limit": 1000}).code,
                             "invalid_input")
            self.assertEqual(dispatcher.dispatch(
                "workspace.list", {"path": ".", "limit": 10, "shell": "id"}
            ).code, "invalid_input")

    def test_symlink_entry_is_reported_without_following_target(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            target = Path(outside)
            (target / "private.txt").write_text("secret", encoding="utf-8")
            link = root / "external-link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available in this environment")
            result = self._dispatcher(root).dispatch("workspace.list", {"path": ".", "limit": 10})
            self.assertTrue(result.ok, result.code)
            self.assertEqual(result.output["entries"], [{"name": "external-link", "type": "symlink"}])
            followed = self._dispatcher(root).dispatch("workspace.list", {"path": "external-link", "limit": 10})
            self.assertFalse(followed.ok)

    def test_reports_truncation_and_enforces_read_only_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(11):
                (root / f"file-{index:02d}.txt").touch()
            result = self._dispatcher(root).dispatch("workspace.list", {"path": ".", "limit": 10})
            self.assertTrue(result.ok, result.code)
            self.assertEqual(len(result.output["entries"]), 10)
            self.assertTrue(result.output["truncated"])
            denied = SkillDispatcher(capability_grants=set())
            register_builtin_workspace_list(denied, root)
            self.assertEqual(denied.dispatch("workspace.list", {"path": ".", "limit": 10}).code,
                             "capability_denied")


if __name__ == "__main__":
    unittest.main()
