import tempfile
import unittest
from pathlib import Path

from automation.readonly_workspace import ReadOnlyWorkspace


class ReadOnlyWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        (self.root / "README.md").write_text("# Yuki\\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=value", encoding="utf-8")
        (self.root / "notes.pem").write_text("private key", encoding="utf-8")
        (self.root / "credentials.json").write_text("cloud secret", encoding="utf-8")
        (self.root / "Secrets.JSON").write_text("another secret", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print('hello')\\n", encoding="utf-8")
        self.workspace = ReadOnlyWorkspace(self.root, max_read_bytes=1024)

    def test_read_and_list_workspace_without_sensitive_files(self):
        listing = self.workspace.list_files()
        names = {entry["name"] for entry in listing["entries"]}
        self.assertIn("README.md", names)
        self.assertIn("src", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("notes.pem", names)
        self.assertNotIn("credentials.json", names)
        self.assertNotIn("Secrets.JSON", names)
        result = self.workspace.read_file("src/main.py")
        self.assertIn("print('hello')", result["content"])

    def test_rejects_parent_absolute_and_symlink_escape(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("not in workspace", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.workspace.read_file("../outside.txt")
        with self.assertRaises(PermissionError):
            self.workspace.read_file(str(outside))
        link = self.root / "escape.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        with self.assertRaises(PermissionError):
            self.workspace.read_file("escape.txt")

    def test_rejects_sensitive_files_and_oversized_content(self):
        with self.assertRaises(PermissionError):
            self.workspace.read_file(".env")
        with self.assertRaises(PermissionError):
            self.workspace.read_file("credentials.json")
        with self.assertRaises(PermissionError):
            self.workspace.read_file("Secrets.JSON")
        (self.root / "large.txt").write_text("x" * 1025, encoding="utf-8")
        with self.assertRaises(ValueError):
            self.workspace.read_file("large.txt")

    def test_hidden_parent_directory_does_not_hide_workspace_files(self):
        hidden_parent = Path(self.tmp.name) / ".private-parent"
        project = hidden_parent / "visible-project"
        project.mkdir(parents=True)
        (project / "README.md").write_text("visible", encoding="utf-8")
        workspace = ReadOnlyWorkspace(project)
        self.assertEqual(workspace.read_file("README.md")["content"], "visible")
        self.assertEqual(
            [entry["name"] for entry in workspace.list_files()["entries"]],
            ["README.md"],
        )

    def test_tool_bundle_is_read_only_and_permission_scoped(self):
        tools = self.workspace.agent_tools()
        self.assertEqual({tool.name for tool in tools}, {"workspace.list", "workspace.read"})
        self.assertTrue(all(tool.permission == "workspace:read" for tool in tools))
        self.assertFalse(any("write" in tool.name or "shell" in tool.name for tool in tools))


if __name__ == "__main__":
    unittest.main()
