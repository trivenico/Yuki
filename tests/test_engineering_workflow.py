import hashlib
import tempfile
import unittest
from pathlib import Path

from automation.approved_workspace_changes import ApprovedTestRunner, ApprovedWorkspaceChanges, FileChange
from automation.authorized_read_tools import AuthorizedReadOnlyTools, ToolPrincipal, ToolAuthorizationError
from automation.engineering_workflow import EngineeringWorkflow


class FakeProvider:
    def __init__(self):
        self.prompts = []

    def is_available(self):
        return True

    def generate(self, prompt):
        self.prompts.append(prompt)
        return "Inspection report: README reviewed; proposed change requires tests."


class EngineeringWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.readme = self.root / "README.md"
        self.readme.write_text("Yuki project.\n", encoding="utf-8")
        (self.root / "test_sample.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            " def test_value(self): self.assertEqual(2 + 2, 4)\n",
            encoding="utf-8",
        )
        self.principal = ToolPrincipal(
            "engineering-worker",
            frozenset({"workspace:read", "workspace:write", "tests:execute"}),
        )
        self.events = []
        self.provider = FakeProvider()
        self.read_tools = AuthorizedReadOnlyTools(
            self.root, audit=self.events.append,
            principal_is_trusted=lambda p: p.actor_id == "engineering-worker",
        )
        self.editor = ApprovedWorkspaceChanges(
            self.root,
            trusted_principal=lambda p: p.actor_id == "engineering-worker",
            approve_change=lambda actor, change_id, digest: actor == self.principal.actor_id and len(digest) == 64,
            audit=self.events.append,
            allowed_paths=("README.md",),
        )
        self.runner = ApprovedTestRunner(
            self.root,
            trusted_principal=lambda p: p.actor_id == "engineering-worker",
            approve_run=lambda actor, run_id, digest: actor == self.principal.actor_id and len(digest) == 64,
            audit=self.events.append,
            allowed_test_modules=("test_sample",),
            timeout_seconds=5,
        )
        self.workflow = EngineeringWorkflow(
            provider=self.provider, read_tools=self.read_tools,
            principal_resolver=lambda: self.principal,
            workspace_changes=self.editor, test_runner=self.runner,
            audit=self.events.append,
        )

    def test_inspect_approve_apply_and_run_allowlisted_tests(self):
        change = FileChange(
            "README.md", hashlib.sha256(self.readme.read_bytes()).hexdigest(),
            "Yuki project updated.\n",
        )
        result = self.workflow.run(
            principal=self.principal,
            task="Review README and apply this approved documentation update.",
            inspection_calls=[{"name": "workspace.read", "arguments": {"path": "README.md"}}],
            proposed_changes=[change],
            test_module="test_sample",
        )
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.change_result.changed_paths, ("README.md",))
        self.assertEqual(self.readme.read_text(encoding="utf-8"), "Yuki project updated.\n")
        self.assertEqual(result.test_result.exit_code, 0)
        self.assertIn("Yuki project.", self.provider.prompts[0])
        stages = [event.get("stage") for event in self.events if "stage" in event]
        self.assertEqual(stages, ["started", "inspection_completed", "changes_applied", "succeeded"])

    def test_inspection_permission_failure_stops_before_edit(self):
        read_only = ToolPrincipal("engineering-worker", frozenset({"workspace:read"}))
        change = FileChange(
            "README.md", hashlib.sha256(self.readme.read_bytes()).hexdigest(),
            "should not apply\n",
        )
        with self.assertRaises(ToolAuthorizationError):
            self.workflow.run(
                principal=read_only, task="Inspect and edit.",
                inspection_calls=[{"name": "workspace.read", "arguments": {"path": "README.md"}}],
                proposed_changes=[change], test_module="test_sample",
            )
        self.assertEqual(self.readme.read_text(encoding="utf-8"), "Yuki project.\n")

    def test_unknown_inspection_tool_stops_before_edit(self):
        change = FileChange(
            "README.md", hashlib.sha256(self.readme.read_bytes()).hexdigest(),
            "should not apply\n",
        )
        with self.assertRaises(PermissionError):
            self.workflow.run(
                principal=self.principal, task="Inspect and edit.",
                inspection_calls=[{"name": "workspace.write", "arguments": {"path": "README.md"}}],
                proposed_changes=[change], test_module="test_sample",
            )
        self.assertEqual(self.readme.read_text(encoding="utf-8"), "Yuki project.\n")


if __name__ == "__main__":
    unittest.main()
