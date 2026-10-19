import json
from pathlib import Path
import tempfile
import unittest

from core.agent import YukiCoreAgent
from core.builtin_manifest_skills import register_builtin_workspace_list, register_builtin_workspace_read_text
from core.manifest_task_agent import ManifestTaskAgent
from core.skill_dispatch import SkillDispatcher


class FakeProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


class BuiltinWorkspaceListTests(unittest.TestCase):
    def _dispatcher(self, workspace):
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"})
        register_builtin_workspace_list(dispatcher, workspace)
        register_builtin_workspace_read_text(dispatcher, workspace)
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

    def test_manifest_planner_executes_only_registered_read_only_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "readme.txt").write_text("not read by the listing tool", encoding="utf-8")
            dispatcher = self._dispatcher(root)
            agent = YukiCoreAgent(dispatcher=dispatcher)
            provider = FakeProvider([
                json.dumps({"type": "tool_call", "skill_id": "workspace.list",
                            "arguments": {"path": ".", "limit": 10}}),
                json.dumps({"type": "final", "message": "I listed the workspace."}),
            ])
            result = ManifestTaskAgent(provider, agent, dispatcher, max_steps=2).run("List workspace files")
            self.assertTrue(result.ok, result.status)
            self.assertEqual(result.status, "completed")
            self.assertIn("readme.txt", provider.prompts[1])
            self.assertNotIn("not read by the listing tool", provider.prompts[1])


    def test_read_text_returns_content_only_for_small_utf8_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("hello Yuki\\nsecond line", encoding="utf-8")
            dispatcher = self._dispatcher(root)
            result = dispatcher.dispatch("workspace.read_text", {"path": "notes.txt", "max_bytes": 1024})
            self.assertTrue(result.ok, result.code)
            self.assertEqual(result.output, {
                "path": "notes.txt", "text": "hello Yuki\\nsecond line", "truncated": False
            })

    def test_read_text_rejects_traversal_hidden_sensitive_and_binary_files(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "notes.txt").write_text("safe", encoding="utf-8")
            (root / ".private.txt").write_text("private", encoding="utf-8")
            (root / "credentials.json").write_text('{"token":"secret"}', encoding="utf-8")
            (root / "sample.bin").write_bytes(b"\\x00\\x01")
            (Path(outside) / "outside.txt").write_text("outside", encoding="utf-8")
            dispatcher = self._dispatcher(root)
            for path in ("../outside.txt", str(Path(outside) / "outside.txt"),
                         ".private.txt", "credentials.json", "sample.bin"):
                with self.subTest(path=path):
                    result = dispatcher.dispatch("workspace.read_text", {"path": path, "max_bytes": 1024})
                    self.assertFalse(result.ok)
                    self.assertIsNone(result.output)

    def test_read_text_rejects_oversized_and_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.txt").write_text("x" * 1200, encoding="utf-8")
            dispatcher = self._dispatcher(root)
            self.assertEqual(dispatcher.dispatch(
                "workspace.read_text", {"path": "large.txt", "max_bytes": 1024}
            ).code, "handler_failed")
            self.assertEqual(dispatcher.dispatch(
                "workspace.read_text", {"path": "notes.txt", "max_bytes": 100000}
            ).code, "invalid_input")

    def test_read_text_rejects_symlink_and_untrusted_capability(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (Path(outside) / "target.txt").write_text("outside", encoding="utf-8")
            link = root / "link.txt"
            try:
                link.symlink_to(Path(outside) / "target.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available in this environment")
            result = self._dispatcher(root).dispatch(
                "workspace.read_text", {"path": "link.txt", "max_bytes": 1024}
            )
            self.assertFalse(result.ok)
            denied = SkillDispatcher(capability_grants=set())
            register_builtin_workspace_read_text(denied, root)
            self.assertEqual(denied.dispatch(
                "workspace.read_text", {"path": "notes.txt", "max_bytes": 1024}
            ).code, "capability_denied")

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
