import json
import tempfile
import unittest
from pathlib import Path

from automation.authorized_read_tools import (
    AuthorizedReadOnlyTools,
    ToolAuthorizationError,
    ToolInputError,
    ToolPrincipal,
)
from automation.specialist_read_tool_adapter import build_read_tool_specialist_handler


class FakeProvider:
    def __init__(self, output="Evidence reviewed."):
        self.output = output
        self.prompts = []
        self.available = True

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.output


class SpecialistReadToolAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "README.md").write_text("Project documentation.\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=do-not-read\n", encoding="utf-8")
        self.audit_events = []
        self.tools = AuthorizedReadOnlyTools(
            self.root,
            audit=self.audit_events.append,
            principal_is_trusted=lambda principal: principal.actor_id == "worker-service",
        )
        self.principal = ToolPrincipal("worker-service", frozenset({"workspace:read"}))
        self.provider = FakeProvider()
        self.handler = build_read_tool_specialist_handler(
            self.provider, self.tools, principal_resolver=lambda: self.principal,
        )

    def request(self, task="Review the source tree.", calls=None):
        return json.dumps({"task": task, "tool_calls": calls or []})

    def test_reads_allowlisted_workspace_file_and_passes_untrusted_evidence(self):
        output = self.handler(self.request(calls=[
            {"name": "workspace.read", "arguments": {"path": "README.md"}},
        ]))
        self.assertEqual(output, "Evidence reviewed.")
        self.assertEqual(len(self.provider.prompts), 1)
        prompt = self.provider.prompts[0]
        self.assertIn("Project documentation.", prompt)
        self.assertIn("untrusted", prompt.lower())
        self.assertIn("Never claim files were changed", prompt)
        self.assertEqual([event["outcome"] for event in self.audit_events], ["started", "succeeded"])

    def test_search_tool_is_available_to_specialist(self):
        self.handler(self.request(calls=[
            {"name": "workspace.search", "arguments": {"query": "greet", "path": "src"}},
        ]))
        self.assertIn("app.py", self.provider.prompts[0])
        self.assertIn("greet", self.provider.prompts[0])

    def test_model_is_never_allowed_to_invent_or_execute_calls(self):
        handler = build_read_tool_specialist_handler(
            self.provider, self.tools, principal_resolver=lambda: self.principal,
            allowed_tool_names=("workspace.read",),
        )
        with self.assertRaises(PermissionError):
            handler(self.request(calls=[
                {"name": "workspace.search", "arguments": {"query": "greet"}},
            ]))
        self.assertEqual(self.provider.prompts, [])

    def test_tool_permission_denial_fails_before_provider_call(self):
        no_permission = ToolPrincipal("worker-service", frozenset())
        handler = build_read_tool_specialist_handler(
            self.provider, self.tools, principal_resolver=lambda: no_permission,
        )
        with self.assertRaises(ToolAuthorizationError):
            handler(self.request(calls=[
                {"name": "workspace.read", "arguments": {"path": "README.md"}},
            ]))
        self.assertEqual(self.provider.prompts, [])

    def test_untrusted_principal_resolution_fails_closed(self):
        handler = build_read_tool_specialist_handler(
            self.provider, self.tools,
            principal_resolver=lambda: ToolPrincipal("attacker", frozenset({"workspace:read"})),
        )
        with self.assertRaises(ToolAuthorizationError):
            handler(self.request(calls=[
                {"name": "workspace.read", "arguments": {"path": "README.md"}},
            ]))
        self.assertEqual(self.provider.prompts, [])

    def test_secret_path_and_path_traversal_are_rejected(self):
        for path in (".env", "../outside.txt"):
            with self.subTest(path=path):
                with self.assertRaises((ToolInputError, PermissionError)):
                    self.handler(self.request(calls=[
                        {"name": "workspace.read", "arguments": {"path": path}},
                    ]))
        self.assertEqual(self.provider.prompts, [])

    def test_input_shape_and_call_count_are_bounded(self):
        with self.assertRaises(ValueError):
            self.handler("not json")
        with self.assertRaises(ValueError):
            self.handler(json.dumps({"task": "x", "extra": "ignored"}))
        too_many = [{"name": "workspace.list", "arguments": {}}] * 6
        with self.assertRaises(ValueError):
            self.handler(self.request(calls=too_many))
        self.assertEqual(self.provider.prompts, [])

    def test_provider_unavailable_or_empty_output_fails(self):
        self.provider.available = False
        with self.assertRaises(RuntimeError):
            self.handler(self.request())
        self.provider.available = True
        self.provider.output = " "
        with self.assertRaises(RuntimeError):
            self.handler(self.request())

    def test_allowlist_must_be_registered_and_nonempty(self):
        with self.assertRaises(ValueError):
            build_read_tool_specialist_handler(
                self.provider, self.tools, principal_resolver=lambda: self.principal,
                allowed_tool_names=("workspace.write",),
            )
        with self.assertRaises(ValueError):
            build_read_tool_specialist_handler(
                self.provider, self.tools, principal_resolver=lambda: self.principal,
                allowed_tool_names=(),
            )


if __name__ == "__main__":
    unittest.main()
