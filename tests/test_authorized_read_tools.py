import os
import tempfile
import unittest
from pathlib import Path

from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway

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

    @unittest.skipUnless(os.name == "posix", "secure descriptor-relative workspace reads are POSIX-only until a Windows handle adapter exists")
    def test_list_and_read_workspace_text(self):
        listed = self.tools.execute(principal=self.principal, tool_name="workspace.list",
                                    arguments={"path": "src"})
        self.assertIn("src/app.py", listed.data["entries"])
        read = self.tools.execute(principal=self.principal, tool_name="workspace.read",
                                  arguments={"path": "src/app.py"})
        self.assertIn("def hello()", read.data["content"])
        self.assertTrue(read.data["untrusted_content"])
        self.assertTrue(read.data["read_only"])

    @unittest.skipUnless(os.name == "posix", "secure descriptor-relative workspace reads are POSIX-only until a Windows handle adapter exists")
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

    @unittest.skipUnless(os.name == "posix", "secure descriptor-relative workspace reads are POSIX-only until a Windows handle adapter exists")
    def test_audit_events_are_bounded_and_recorded(self):
        self.tools.execute(principal=self.principal, tool_name="workspace.read",
                           arguments={"path": "README.md"})
        self.assertEqual(self.audit_events[0]["outcome"], "started")
        self.assertEqual(self.audit_events[-1]["outcome"], "succeeded")
        self.assertEqual(self.audit_events[-1]["actor_id"], "alice")

    def test_workspace_reads_reject_in_workspace_symlink(self):
        target = self.root / "src" / "app.py"
        link = self.root / "inside-link.py"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this runner")
        with self.assertRaises(ToolInputError):
            self.tools.execute(principal=self.principal, tool_name="workspace.read",
                               arguments={"path": "inside-link.py"})

    @unittest.skipUnless(os.name == "posix", "secure descriptor-relative workspace reads are POSIX-only until a Windows handle adapter exists")
    def test_central_gateway_authorizes_canonical_workspace_resource(self):
        class Policy:
            def __init__(self):
                self.requests = []
            def authorize(self, request):
                self.requests.append(request)
                return AuthorizationDecision(True, "decision-1", "policy-v1", "allowed")

        policy = Policy()
        gateway = AuthorizationGateway(policy, audit=lambda *_: None)
        tools = AuthorizedReadOnlyTools(
            self.root,
            audit=lambda _event: None,
            principal_is_trusted=lambda principal: principal.actor_id == "alice",
            authorization_gateway=gateway,
            principal_for_tool_principal=lambda principal: Principal(principal.actor_id, "human"),
            policy_revision="policy-v1",
            task_id="task-8",
            delegation_id="grant-4",
            approval_id="approval-2",
        )
        result = tools.execute(principal=self.principal, tool_name="workspace.read",
                               arguments={"path": "src/app.py"})
        self.assertIn("def hello()", result.data["content"])
        request = policy.requests[0]
        self.assertEqual(request.action, "workspace:read")
        self.assertEqual(request.resource, "workspace:src/app.py")
        self.assertEqual(request.task_id, "task-8")
        self.assertEqual(request.delegation_id, "grant-4")
        self.assertEqual(request.approval_id, "approval-2")
        self.assertEqual(request.expected_policy_revision, "policy-v1")

    def test_central_gateway_denial_prevents_file_read(self):
        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(False, "decision-2", "policy-v1", "denied")

        gateway = AuthorizationGateway(Policy(), audit=lambda *_: None)
        tools = AuthorizedReadOnlyTools(
            self.root,
            audit=lambda _event: None,
            principal_is_trusted=lambda principal: principal.actor_id == "alice",
            authorization_gateway=gateway,
            principal_for_tool_principal=lambda principal: Principal(principal.actor_id, "human"),
            policy_revision="policy-v1",
        )
        with self.assertRaises(ToolAuthorizationError):
            tools.execute(principal=self.principal, tool_name="workspace.read",
                          arguments={"path": "README.md"})

    def test_gateway_and_principal_resolver_must_be_paired(self):
        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "decision", "v1", "allowed")
        gateway = AuthorizationGateway(Policy(), audit=lambda *_: None)
        with self.assertRaises(ValueError):
            AuthorizedReadOnlyTools(
                self.root, audit=lambda _event: None,
                principal_is_trusted=lambda _principal: True,
                authorization_gateway=gateway,
            )


    @unittest.skipUnless(os.name == "nt", "Windows-specific fail-closed regression")
    def test_windows_workspace_read_fails_closed_without_secure_handle_adapter(self):
        with self.assertRaises(ToolInputError):
            self.tools.execute(
                principal=self.principal,
                tool_name="workspace.read",
                arguments={"path": "README.md"},
            )


if __name__ == "__main__":
    unittest.main()
