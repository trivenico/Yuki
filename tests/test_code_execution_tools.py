import tempfile
import unittest
from pathlib import Path

from automation.authorized_read_tools import AuthorizedReadOnlyTools, ToolPrincipal
from core.code_execution import CodeExecutionContractError, CodeExecutionRequest
from core.code_execution_tools import AuthenticatedReadToolDispatcher


class AuthenticatedReadToolDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "README.md").write_text("Yuki read-only registry", encoding="utf-8")
        self.events = []
        self.principal = ToolPrincipal("account-a", frozenset({"workspace:read"}))
        self.tools = AuthorizedReadOnlyTools(
            self.root,
            audit=self.events.append,
            principal_is_trusted=lambda principal: principal.actor_id == "account-a",
        )
        self.request = CodeExecutionRequest(
            execution_id="exec-001",
            actor_id="account-a",
            session_id="a" * 64,
            task_id="task-001",
            source="result = await tools.workspace.read({path: 'README.md'})",
            tool_names=("workspace.read",),
            max_tool_calls=1,
        )
        self.dispatcher = AuthenticatedReadToolDispatcher(
            self.tools,
            principal_resolver=lambda _request: self.principal,
        )

    def test_exposes_only_existing_read_only_registry(self):
        self.assertEqual(
            self.dispatcher.tool_names,
            ("workspace.list", "workspace.read", "workspace.search"),
        )
        self.assertEqual(
            self.dispatcher.dispatch(
                self.request, "workspace.read", {"path": "README.md"}
            )["content"],
            "Yuki read-only registry",
        )
        self.assertEqual(self.events[0]["outcome"], "started")
        self.assertEqual(self.events[-1]["outcome"], "succeeded")


    def test_bind_produces_service_compatible_host_callback(self):
        dispatch_tool = self.dispatcher.bind(self.request)
        result = dispatch_tool("workspace.read", {"path": "README.md"})
        self.assertEqual(result["content"], "Yuki read-only registry")

    def test_manifest_denial_precedes_principal_resolution(self):
        called = []
        dispatcher = AuthenticatedReadToolDispatcher(
            self.tools, principal_resolver=lambda request: called.append(request)
        )
        with self.assertRaises(CodeExecutionContractError):
            dispatcher.dispatch(self.request, "workspace.search", {"query": "Yuki"})
        self.assertEqual(called, [])
        self.assertEqual(self.events, [])

    def test_cross_account_principal_is_rejected(self):
        other_request = CodeExecutionRequest(
            execution_id="exec-002",
            actor_id="account-b",
            session_id="b" * 64,
            task_id="task-002",
            source="result = 1",
            tool_names=("workspace.read",),
        )
        with self.assertRaisesRegex(CodeExecutionContractError, "does not own"):
            self.dispatcher.dispatch(other_request, "workspace.read", {"path": "README.md"})
        self.assertEqual(self.events, [])

    def test_invalid_principal_fails_closed(self):
        dispatcher = AuthenticatedReadToolDispatcher(
            self.tools, principal_resolver=lambda _request: {"actor_id": "account-a"}
        )
        with self.assertRaisesRegex(CodeExecutionContractError, "ToolPrincipal"):
            dispatcher.dispatch(self.request, "workspace.read", {"path": "README.md"})
        self.assertEqual(self.events, [])

    def test_registry_rechecks_permission_and_trust(self):
        no_permission = ToolPrincipal("account-a", frozenset())
        dispatcher = AuthenticatedReadToolDispatcher(
            self.tools, principal_resolver=lambda _request: no_permission
        )
        with self.assertRaises(PermissionError):
            dispatcher.dispatch(self.request, "workspace.read", {"path": "README.md"})
        self.assertEqual(self.events[-1]["code"], "missing_permission")


if __name__ == "__main__":
    unittest.main()
