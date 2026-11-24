import unittest
import tempfile
from pathlib import Path

from core.authorization import Principal
from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.production_security import ProductionSecurityContext


class Policy:
    def authorize(self, request):
        raise AssertionError("policy is not called by identity resolvers")


class ProductionSecurityContextTests(unittest.TestCase):
    def context(self, **overrides):
        values = {
            "authorization_gateway": AuthorizationGateway(Policy(), audit=SQLiteAuthorizationAuditLog(str(Path(tempfile.mkdtemp()) / "authorization-audit.sqlite3"))),
            "principal_for_actor": lambda actor: Principal(actor, "human"),
            "principal_for_worker": lambda worker: Principal(
                worker, "workload", tenant_id="tenant-1", agent_id="agent-1",
            ),
            "policy_revision": "revision-1",
        }
        values.update(overrides)
        return ProductionSecurityContext(**values)

    def test_resolves_authenticated_actor_without_changing_identity(self):
        context = self.context()
        self.assertEqual(context.require_actor("alice").principal_id, "alice")

    def test_rejects_actor_resolver_identity_mismatch(self):
        context = self.context(principal_for_actor=lambda _actor: Principal("mallory", "human"))
        with self.assertRaises(PermissionError):
            context.require_actor("alice")

    def test_resolves_workload_principal(self):
        context = self.context()
        principal = context.require_worker("worker-1")
        self.assertEqual(principal.kind, "workload")
        self.assertEqual(principal.principal_id, "worker-1")

    def test_rejects_non_workload_or_unscoped_worker_principal(self):
        for principal in (
            Principal("worker-1", "human"),
            Principal("worker-1", "workload", tenant_id="tenant-1"),
        ):
            context = self.context(principal_for_worker=lambda _worker, p=principal: p)
            with self.assertRaises(PermissionError):
                context.require_worker("worker-1")

    def test_rejects_non_durable_authorization_audit_sink(self):
        gateway = AuthorizationGateway(Policy(), audit=lambda *_: None)
        with self.assertRaisesRegex(TypeError, "durable SQLiteAuthorizationAuditLog"):
            self.context(authorization_gateway=gateway)

    def test_requires_gateway_and_trusted_resolvers(self):
        with self.assertRaises(TypeError):
            self.context(authorization_gateway=object())
        with self.assertRaises(TypeError):
            self.context(principal_for_worker=None)
        with self.assertRaises(ValueError):
            self.context(policy_revision=" ")


if __name__ == "__main__":
    unittest.main()
