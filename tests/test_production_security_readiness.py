import asyncio
import tempfile
import unittest
from pathlib import Path

from core.authorization import Principal
from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.production_security import ProductionSecurityContext
from core.production_security_readiness import ProductionSecurityReadinessASGI


class MemoryAnchor:
    def __init__(self):
        self.checkpoint = None

    def publish(self, checkpoint):
        self.checkpoint = checkpoint

    def latest(self):
        return self.checkpoint


class Policy:
    def authorize(self, request):
        raise AssertionError("readiness must not invoke authorization policy")


class ProductionSecurityReadinessTests(unittest.TestCase):
    def setUp(self):
        self.audit = SQLiteAuthorizationAuditLog(
            str(Path(tempfile.mkdtemp()) / "authorization-audit.sqlite3")
        )
        self.anchor = MemoryAnchor()
        self.audit.publish_checkpoint(self.anchor)
        self.context = ProductionSecurityContext(
            authorization_gateway=AuthorizationGateway(Policy(), audit=self.audit),
            audit_anchor=self.anchor,
            principal_for_actor=lambda actor: Principal(actor, "human"),
            principal_for_worker=lambda worker: Principal(
                worker, "workload", tenant_id="tenant", agent_id="agent"
            ),
            policy_revision="revision-1",
        )
        self.app = ProductionSecurityReadinessASGI(self.context)

    def request(self, path, method="GET"):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(self.app(
            {"type": "http", "method": method, "path": path, "headers": []},
            receive, send,
        ))
        return sent[0]["status"], dict(sent[0]["headers"]), sent[1]["body"]

    def test_ready_when_audit_chain_matches_fresh_external_checkpoint(self):
        status, headers, body = self.request("/health/ready")
        self.assertEqual(status, 200)
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual(body, b'{"status":"ready"}')

    def test_readiness_fails_closed_after_audit_chain_tampering_but_liveness_survives(self):
        with self.audit._connect() as db:
            db.execute(
                "INSERT INTO authorization_audit_events "
                "(occurred_at, principal_hash, action, resource_hash, risk, allowed, "
                "decision_hash, policy_revision_hash, reason_code, previous_hash, event_hash) "
                "VALUES ('now','p','task:read','r','low',1,'d','v','allowed',?,?)",
                ("0" * 64, "f" * 64),
            )
        status, _, body = self.request("/health/ready")
        self.assertEqual(status, 503)
        self.assertEqual(body, b'{"status":"not_ready"}')
        status, _, body = self.request("/health/live")
        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"status":"alive"}')

    def test_only_get_is_allowed_and_unknown_paths_are_not_exposed(self):
        self.assertEqual(self.request("/health/ready", "POST")[0], 405)
        self.assertEqual(self.request("/admin")[0], 404)


if __name__ == "__main__":
    unittest.main()
