import json
import tempfile
import unittest
from pathlib import Path

from core.guarded_tool_runtime import GuardedToolRuntime
from core.tool_action_context import ToolActionContext, ToolActionContextError
from core.tool_action_journal import ToolActionJournal
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog, ToolEffect


class Policy:
    def authorize_tool(self, name):
        return True


class ToolActionContextTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.journal = ToolActionJournal(str(Path(self.temp_dir.name) / "actions.sqlite3"))
        self.approvals = SQLiteToolActionApprovalStore(str(Path(self.temp_dir.name) / "approvals.sqlite3"))
        self.request = {"to": "person@example.com", "subject": "Hello"}
        self.context = ToolActionContext.create("user-123", "email.send", self.request)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _runtime(self, handler, *, confirmation=False):
        return GuardedToolRuntime(
            {"email.send": handler},
            Policy(),
            capabilities=ToolCapabilityCatalog({
                "email.send": ToolCapability(
                    ToolEffect.SIDE_EFFECTING, requires_user_confirmation=confirmation,
                ),
            }),
            require_action_context=True,
            action_journal=self.journal,
            approval_store=self.approvals,
        )

    def test_context_binds_principal_tool_and_canonical_request(self):
        self.context.verify("user-123", "email.send", {"subject": "Hello", "to": "person@example.com"})
        with self.assertRaises(ToolActionContextError):
            self.context.verify("user-456", "email.send", self.request)
        with self.assertRaises(ToolActionContextError):
            self.context.verify("user-123", "calendar.create", self.request)
        with self.assertRaises(ToolActionContextError):
            self.context.verify("user-123", "email.send", {"to": "other@example.com"})

    def test_distinct_intentional_actions_get_distinct_host_ids(self):
        other = ToolActionContext.create("user-123", "email.send", self.request)
        self.assertNotEqual(self.context.action_id, other.action_id)
        self.assertNotEqual(self.context.idempotency_key, other.idempotency_key)

    def test_invalid_principal_tool_and_non_json_requests_are_rejected(self):
        for principal in ("", "bad\nprincipal", None):
            with self.subTest(principal=principal), self.assertRaises(ToolActionContextError):
                ToolActionContext.create(principal, "email.send", self.request)
        with self.assertRaises(ToolActionContextError):
            ToolActionContext.create("user-123", "../send", self.request)
        with self.assertRaises(ToolActionContextError):
            ToolActionContext.create("user-123", "email.send", {"bad": float("nan")})

    def test_non_string_nested_object_keys_are_rejected(self):
        with self.assertRaises(ToolActionContextError):
            ToolActionContext.create("user-123", "email.send", {"nested": {1: "bad"}})

    def test_context_is_immutable(self):
        with self.assertRaises((AttributeError, TypeError)):
            self.context.principal_id = "attacker"

    def test_side_effect_is_reserved_executed_and_replayed_without_duplicate_handler_call(self):
        calls = []
        runtime = self._runtime(lambda argument: calls.append(argument) or "sent")
        argument = json.dumps(self.request, separators=(",", ":"))
        self.assertIn("required", runtime.invoke("email.send", argument))
        self.assertIn(
            "does not match",
            runtime.invoke(
                "email.send", argument,
                action_context=self.context, principal_id="another-user",
            ),
        )
        kwargs = {"action_context": self.context, "principal_id": "user-123"}
        self.assertEqual(runtime.invoke("email.send", argument, **kwargs), "sent")
        self.assertEqual(runtime.invoke("email.send", argument, **kwargs), "sent")
        self.assertEqual(len(calls), 1)
        action = self.journal.get(self.context.idempotency_key)
        self.assertEqual(action.state, "succeeded")
        self.assertEqual(action.action_id, self.context.action_id)

    def test_duplicate_json_keys_are_rejected_before_journal_reservation(self):
        runtime = self._runtime(lambda _: "sent")
        argument = '{"to":"person@example.com","to":"attacker@example.com","subject":"Hello"}'
        result = runtime.invoke(
            "email.send", argument,
            action_context=self.context, principal_id="user-123",
        )
        self.assertIn("does not match", result)
        self.assertIsNone(self.journal.get(self.context.idempotency_key))

    def test_confirmation_is_required_before_journal_reservation(self):
        runtime = self._runtime(lambda _: "sent", confirmation=True)
        argument = json.dumps(self.request, separators=(",", ":"))
        kwargs = {"action_context": self.context, "principal_id": "user-123"}
        self.assertIn("confirmation", runtime.invoke("email.send", argument, **kwargs))
        self.assertIsNone(self.journal.get(self.context.idempotency_key))
        token = self.approvals.issue(self.context, principal_id="user-123")
        self.assertEqual(runtime.invoke("email.send", argument, approval_id=token, **kwargs), "sent")

    def test_journal_conflict_does_not_consume_confirmation_approval(self):
        calls = []
        runtime = self._runtime(
            lambda argument: calls.append(argument) or "sent", confirmation=True,
        )
        argument = json.dumps(self.request, separators=(",", ":"))
        token = self.approvals.issue(self.context, principal_id="user-123")
        # Simulate a durable record that conflicts with this trusted action ID.
        self.journal.reserve(
            self.context.idempotency_key, "email.send", self.request,
            action_id="a" * 32,
        )
        result = runtime.invoke(
            "email.send", argument, action_context=self.context,
            principal_id="user-123", approval_id=token,
        )
        self.assertIn("blocked", result.lower())
        self.assertEqual(calls, [])
        # The failed reservation happened before token consumption.
        self.assertTrue(
            self.approvals.consume(token, self.context, principal_id="user-123")
        )

    def test_competing_request_does_not_consume_approval_without_action_claim(self):
        runtime = self._runtime(lambda _: "sent", confirmation=True)
        argument = json.dumps(self.request, separators=(",", ":"))
        token = self.approvals.issue(self.context, principal_id="user-123")
        reserved = self.journal.reserve(
            self.context.idempotency_key, "email.send", self.request,
            action_id=self.context.action_id,
        )
        held = self.journal.claim_execution(
            self.context.idempotency_key, reserved.request_digest,
        )
        result = runtime.invoke(
            "email.send", argument, action_context=self.context,
            principal_id="user-123", approval_id=token,
        )
        self.assertIn("blocked", result.lower())
        self.assertEqual(self.journal.get(self.context.idempotency_key).state, "authorizing")
        self.assertTrue(
            self.approvals.consume(token, self.context, principal_id="user-123")
        )
        self.journal.release_authorization_claim(
            self.context.idempotency_key, held.lease_owner,
        )

    def test_handler_failure_marks_outcome_unknown_and_prevents_replay(self):
        calls = []
        def fail(_):
            calls.append("called")
            raise RuntimeError("provider timeout")
        runtime = self._runtime(fail)
        argument = json.dumps(self.request, separators=(",", ":"))
        kwargs = {"action_context": self.context, "principal_id": "user-123"}
        self.assertIn("reconciliation", runtime.invoke("email.send", argument, **kwargs))
        self.assertIn("reconciliation", runtime.invoke("email.send", argument, **kwargs))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.journal.get(self.context.idempotency_key).state, "outcome_unknown")

    def test_read_only_tools_do_not_require_action_context(self):
        runtime = GuardedToolRuntime(
            {"email.list": lambda _: "[]"},
            Policy(),
            capabilities=ToolCapabilityCatalog({
                "email.list": ToolCapability(ToolEffect.READ_ONLY),
            }),
            require_action_context=False,
        )
        self.assertEqual(runtime.invoke("email.list", "{}"), "[]")

    def test_strict_mode_requires_catalog_and_journal(self):
        with self.assertRaises(ValueError):
            GuardedToolRuntime({"email.send": lambda _: "sent"}, Policy(), require_action_context=True)
        with self.assertRaises(ValueError):
            GuardedToolRuntime(
                {"email.send": lambda _: "sent"}, Policy(),
                capabilities=ToolCapabilityCatalog({
                    "email.send": ToolCapability(ToolEffect.SIDE_EFFECTING),
                }),
                require_action_context=True,
            )


if __name__ == "__main__":
    unittest.main()
