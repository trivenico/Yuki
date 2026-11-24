import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
from core.authorization_audit import SQLiteAuthorizationAuditLog


class SQLiteAuthorizationAuditLogTests(unittest.TestCase):
    def test_gateway_events_persist_and_chain_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "authorization.sqlite3")
            first = SQLiteAuthorizationAuditLog(path)
            request = AuthorizationRequest(
                Principal("alice", "human", tenant_id="tenant-1"),
                "tool:invoke", "tool:private-resource", risk="high",
                task_id="task-1", expected_policy_revision="revision-7",
            )
            decision = AuthorizationDecision(True, "decision-secret", "revision-7", "allowed")
            self.assertEqual(first(request, decision), 1)
            second = SQLiteAuthorizationAuditLog(path)
            self.assertEqual(second.head_hash(), first.head_hash())
            self.assertTrue(second.verify_chain())
            with sqlite3.connect(path) as db:
                row = db.execute(
                    "SELECT principal_hash, resource_hash, decision_hash, allowed FROM authorization_audit_events"
                ).fetchone()
            self.assertEqual(row[0], SQLiteAuthorizationAuditLog._digest("alice"))
            self.assertEqual(row[1], SQLiteAuthorizationAuditLog._digest("tool:private-resource"))
            self.assertEqual(row[2], SQLiteAuthorizationAuditLog._digest("decision-secret"))
            self.assertEqual(row[3], 1)
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"alice", raw)
            self.assertNotIn(b"tool:private-resource", raw)
            self.assertNotIn(b"decision-secret", raw)

    def test_chain_detects_row_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "authorization.sqlite3")
            audit = SQLiteAuthorizationAuditLog(path)
            audit(
                AuthorizationRequest(Principal("alice", "human"), "task:create", "task:collection"),
                AuthorizationDecision(False, "decision-1", "revision-1", "denied"),
            )
            self.assertTrue(audit.verify_chain())
            with sqlite3.connect(path) as db:
                db.execute("UPDATE authorization_audit_events SET reason_code='tampered'")
            self.assertFalse(audit.verify_chain())

    def test_concurrent_instances_append_a_single_valid_chain(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "authorization.sqlite3")
            writers = [SQLiteAuthorizationAuditLog(path) for _ in range(4)]
            def append(index):
                request = AuthorizationRequest(
                    Principal("worker-" + str(index), "workload", tenant_id="tenant-1", agent_id="agent-1"),
                    "task:read", "task:" + str(index),
                )
                return writers[index % len(writers)](
                    request, AuthorizationDecision(True, "decision-" + str(index), "revision-1", "allowed")
                )
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                ids = list(pool.map(append, range(40)))
            self.assertEqual(len(set(ids)), 40)
            self.assertTrue(writers[0].verify_chain())
            self.assertEqual(writers[0].head_hash(), writers[-1].head_hash())


if __name__ == "__main__":
    unittest.main()
