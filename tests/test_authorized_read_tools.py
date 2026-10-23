import tempfile
import unittest
from pathlib import Path

from automation.authorized_read_tools import (
    AuthorizedReadOnlyTools, ToolAuthorizationError, ToolInputError, ToolPrincipal,
)


class AuthorizedReadOnlyToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workspace"
        self.root.mkdir()
        (self.root / "README.md").write_text("# Yuki\nread-only tool test\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
        (self.root / ".env").write_text("DO_NOT_EXPOSE=secret\n", encoding="utf-8")
        self.audit_events = []
        self.principal = ToolPrincipal("alice", frozenset({"workspace:read"}))
        self.tools = AuthorizedReadOnlyTools(
            self.root,
            audit=self.audit_events.append,
            principal_is_trusted=lambda principal: principal.actor_id == "alice",
        )

    def test_registry_is_explicit_and_read_only(self):
        self.assertEqual(
            self.tools.tool_names,
            ("workspace.list", "workspace.read", "workspace.search"),
        )
        self.assertFalse(any("write" in name or "shell" in name or "exec" in name
                             for name in self.tools.tool_names))

    def test_list_and_read_workspace_text(self):
        listed = self.tools.execute(principal=self.principal, tool_name="workspace.list",
                                    arguments={"path": "src"})
        self.assertIn("src/app.py", listed.data["entries"])
        read = self.tools.execute(principal=self.principal, tool_name="workspace.read",
                                  arguments={"path": "src/app.py"})
        self.assertIn("def hello()", read.data["content"])
        self.assertTrue(read.data["untrusted_content"])
        self.assertTrue(read.data["read_only"])

    def test_search_returns_bounded_matches(self):
        result = self.tools.execute(principal=self.principal, tool_name="workspace.search",
                                    arguments={"query": "hello"})
        self.assertTrue(any(match["path"] == "src/app.py" for match in result.data["matches"]))
        self.assertNotIn(".env", str(result.data))

    def test_permission_and_trusted_identity_fail_closed(self):
        untrusted = ToolPrincipal("mallory", frozenset({"workspace:read"}))
        with self.assertRaises(ToolAuthorizationError):
            self.tools.execute(principal=untrusted, tool_name="workspace.read",
                               arguments={"path": "README.md"})
        no_permission = ToolPrincipal("alice", frozenset())
        with self.assertRaises(ToolAuthorizationError):
            self.tools.execute(principal=no_permission, tool_name="workspace.read",
                               arguments={"path": "README.md"})
        with self.assertRaises(ToolAuthorizationError):
            self.tools.execute(principal={"actor_id": "alice", "permissions": ["workspace:read"]},
                               tool_name="workspace.read", arguments={"path": "README.md"})

    def test_path_traversal_and_secret_files_are_rejected(self):
        for path in ("../outside.txt", ".env", ".env.production", ".git/config"):
            with self.subTest(path=path):
                with self.assertRaises(ToolInputError):
                    self.tools.execute(principal=self.principal, tool_name="workspace.read",
                                       arguments={"path": path})

    def test_unknown_tool_is_denied(self):
        with self.assertRaises(ToolAuthorizationError):
            self.tools.execute(principal=self.principal, tool_name="workspace.write",
                               arguments={"path": "README.md", "content": "changed"})
        self.assertFalse(any("workspace.write" == name for name in self.tools.tool_names))

    def test_audit_failure_prevents_handler_execution(self):
        tools = AuthorizedReadOnlyTools(
            self.root,
            audit=lambda _event: (_ for _ in ()).throw(RuntimeError("audit down")),
            principal_is_trusted=lambda _principal: True,
        )
        with self.assertRaisesRegex(RuntimeError, "audit is unavailable"):
            tools.execute(principal=self.principal, tool_name="workspace.read",
                          arguments={"path": "README.md"})

    def test_input_and_file_size_limits(self):
        with self.assertRaises(ToolInputError):
            self.tools.execute(principal=self.principal, tool_name="workspace.search",
                               arguments={"query": "x" * 301})
        large = self.root / "large.txt"
        large.write_text("x" * (256 * 1024 + 1), encoding="utf-8")
        with self.assertRaises(ToolInputError):
            self.tools.execute(principal=self.principal, tool_name="workspace.read",
                               arguments={"path": "large.txt"})

    def test_symlink_cannot_escape_workspace(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("not for this tool", encoding="utf-8")
        link = self.root / "outside-link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this runner")
        with self.assertRaises(ToolInputError):
            self.tools.execute(principal=self.principal, tool_name="workspace.read",
                               arguments={"path": "outside-link.txt"})

    def test_audit_events_are_bounded_and_recorded(self):
        self.tools.execute(principal=self.principal, tool_name="workspace.read",
                           arguments={"path": "README.md"})
        self.assertEqual(self.audit_events[0]["outcome"], "started")
        self.assertEqual(self.audit_events[-1]["outcome"], "succeeded")
        self.assertEqual(self.audit_events[-1]["actor_id"], "alice")


if __name__ == "__main__":
    unittest.main()
