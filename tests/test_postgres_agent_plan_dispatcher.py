from __future__ import annotations

import hashlib
import unittest
from types import MappingProxyType, SimpleNamespace

from automation.postgres_agent_plan_adapter import ClaimedAgentPlanDispatch
from automation.postgres_agent_plan_dispatcher import (
    PermanentDispatchError,
    PostgresAgentPlanOutboxDispatcher,
)


class PostgresAgentPlanDispatcherContractTests(unittest.TestCase):
    def _claim(self, envelope):
        return ClaimedAgentPlanDispatch(
            tenant_id="tenant-a",
            owner_principal_id="human:alice",
            plan_id="plan-a",
            subtask_id="research",
            reservation_token="r" * 40,
            dispatch_generation=1,
            claim_token="c" * 40,
            claim_generation=1,
            envelope_version=1,
            envelope_digest="a" * 64,
            envelope=MappingProxyType(envelope),
        )

    def _envelope(self):
        return {
            "version": 1,
            "tenant_id": "tenant-a",
            "owner_principal_id": "human:alice",
            "plan_id": "plan-a",
            "subtask_id": "research",
            "reservation_generation": 1,
            "idempotency_key": "agent-plan:stable:research",
            "agent_id": "researcher",
            "action": "emit",
            "manifest_revision": 3,
            "required_capabilities": ("research",),
            "required_permissions": ("web:read",),
            "requires_approval": True,
            "dependency_snapshot": (),
            "dependency_output_digests": MappingProxyType({}),
            "argument": "bounded task input",
        }

    def test_valid_immutable_envelope_is_accepted(self):
        dispatcher = object.__new__(PostgresAgentPlanOutboxDispatcher)
        claim = self._claim(self._envelope())
        self.assertIs(dispatcher._validate_envelope(claim), claim.envelope)

    def test_envelope_scope_mismatch_is_permanent(self):
        dispatcher = object.__new__(PostgresAgentPlanOutboxDispatcher)
        envelope = self._envelope()
        envelope["owner_principal_id"] = "human:mallory"
        with self.assertRaises(PermanentDispatchError) as raised:
            dispatcher._validate_envelope(self._claim(envelope))
        self.assertEqual("envelope_scope_mismatch", raised.exception.failure_code)

    def test_non_emit_action_is_rejected_before_task_creation(self):
        dispatcher = object.__new__(PostgresAgentPlanOutboxDispatcher)
        envelope = self._envelope()
        envelope["action"] = "shell.execute"
        with self.assertRaises(PermanentDispatchError) as raised:
            dispatcher._validate_envelope(self._claim(envelope))
        self.assertEqual("envelope_fields_invalid", raised.exception.failure_code)

    def test_dependency_digest_must_be_lowercase_sha256(self):
        dispatcher = object.__new__(PostgresAgentPlanOutboxDispatcher)
        envelope = self._envelope()
        envelope["dependency_output_digests"] = MappingProxyType({"prior": "A" * 64})
        with self.assertRaises(PermanentDispatchError) as raised:
            dispatcher._validate_envelope(self._claim(envelope))
        self.assertEqual("dependency_digest_invalid", raised.exception.failure_code)

    def test_dependency_digest_uses_coordinator_normalization(self):
        dispatcher = object.__new__(PostgresAgentPlanOutboxDispatcher)
        output = "  hello\\x00 "
        normalized = output.replace("\\x00", " ").strip()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        envelope = self._envelope()
        envelope["dependency_snapshot"] = (
            MappingProxyType({"subtask_id": "prior", "task_id": "task-prior", "state": "succeeded"}),
        )
        envelope["dependency_output_digests"] = MappingProxyType({"prior": digest})

        class Runtime:
            def get(self, *, actor_id, task_id):
                self.asserted = (actor_id, task_id)
                return SimpleNamespace(state="succeeded", result={"output": output})

        runtime = Runtime()
        dispatcher.runtime = runtime
        dispatcher._verify_dependencies(
            self._claim(envelope), envelope, SimpleNamespace(principal_id="human:alice")
        )
        self.assertEqual(("human:alice", "task-prior"), runtime.asserted)

    def test_failure_codes_cannot_contain_exception_or_secret_text(self):
        for invalid in ("", "database password leaked", "TOKEN=abc", "Bad-Code", "x" * 81):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    PermanentDispatchError(invalid)


if __name__ == "__main__":
    unittest.main()
