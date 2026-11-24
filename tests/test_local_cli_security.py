import tempfile
import unittest
from pathlib import Path

from automation.local_worker_runtime import build_local_worker_runtime
from core.authorization import AuthorizationRequest
from core.local_cli_security import (
    LOCAL_OPERATOR,
    LOCAL_WORKER,
    build_local_cli_security_context,
)


class FakeProvider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "bounded local result"


class LocalCliSecurityBootstrapTests(unittest.TestCase):
    def test_local_context_authorizes_only_named_operator_and_worker_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            security = build_local_cli_security_context(directory)
            operator = security.require_actor(LOCAL_OPERATOR)
            worker = security.require_worker(LOCAL_WORKER)

            allowed = security.authorization_gateway.authorize(
                AuthorizationRequest(operator, "task:create", "task:collection")
            )
            denied = security.authorization_gateway.authorize(
                AuthorizationRequest(operator, "security:admin", "system")
            )
            worker_allowed = security.authorization_gateway.authorize(
                AuthorizationRequest(worker, "agent:worker:claim", "agent-worker:" + LOCAL_WORKER)
            )

            self.assertTrue(allowed.allowed)
            self.assertFalse(denied.allowed)
            self.assertTrue(worker_allowed.allowed)
            self.assertTrue(security.authorization_gateway.audit_writer.verify_chain())
            security.verify_audit_integrity()

    def test_local_context_rejects_unrecognized_actor_and_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            security = build_local_cli_security_context(directory)
            with self.assertRaises(PermissionError):
                security.require_actor("unrecognized-operator")
            with self.assertRaises(PermissionError):
                security.require_worker("unrecognized-worker")

    def test_cli_worker_runtime_receives_explicit_local_security_context(self):
        with tempfile.TemporaryDirectory() as directory:
            security = build_local_cli_security_context(directory)
            built = build_local_worker_runtime(
                Path(directory), FakeProvider(), security=security,
            )
            self.assertIs(built.runtime.authorization_gateway, security.authorization_gateway)
            self.assertEqual(built.operator_actor_id, LOCAL_OPERATOR)


if __name__ == "__main__":
    unittest.main()
