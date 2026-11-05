"""Crash-recovery tests for durable provider execution correlation."""
import tempfile
import unittest
from pathlib import Path

from providers.durable_provider_governance import SQLiteProviderGovernanceStore


class DurableProviderExecutionRecordTests(unittest.TestCase):
    def test_expired_running_execution_becomes_uncertain_not_replayed(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderGovernanceStore(
                str(Path(directory) / "provider.sqlite3"),
                lease_timeout_seconds=5,
                clock=lambda: now[0],
            )
            lease = store.acquire(provider_id="local.engine", agent_id="planner")
            active = store.list_executions(provider_id="local.engine")
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["execution_id"], lease.execution_id)
            self.assertEqual(active[0]["status"], "running")

            now[0] += 6
            unresolved = store.list_executions(provider_id="local.engine")
            self.assertEqual(len(unresolved), 1)
            self.assertEqual(unresolved[0]["execution_id"], lease.execution_id)
            self.assertEqual(unresolved[0]["status"], "uncertain")
            self.assertEqual(unresolved[0]["reason"], "lease_expired")
            self.assertEqual(store.snapshot(
                provider_id="local.engine", agent_id="planner"
            )["global_active_requests"], 0)

    def test_uncertain_execution_requires_evidence_to_resolve(self):
        import hashlib
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderGovernanceStore(
                str(Path(directory) / "provider.sqlite3"),
                lease_timeout_seconds=5, clock=lambda: now[0],
            )
            lease = store.acquire(provider_id="local.engine", agent_id="planner")
            now[0] += 6
            self.assertEqual(store.list_executions(provider_id="local.engine")[0]["status"], "uncertain")
            evidence = b"provider operation status: completed"
            self.assertTrue(store.resolve_uncertain_execution(
                lease.execution_id, outcome="succeeded", evidence_source="provider_api",
                evidence_sha256=hashlib.sha256(evidence).hexdigest(),
                provider_reference="request-123", resolver_id="operator-1",
            ))
            resolved = store.list_executions(
                provider_id="local.engine", statuses=("succeeded",)
            )
            self.assertEqual(resolved[0]["reason"], "evidence_reconciled")
            self.assertEqual(len(store.list_resolutions(execution_id=lease.execution_id)), 1)
            self.assertEqual(store.list_executions(provider_id="local.engine"), [])

    def test_resolution_rejects_invalid_evidence_and_non_uncertain_state(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderGovernanceStore(str(Path(directory) / "provider.sqlite3"))
            lease = store.acquire(provider_id="local.engine", agent_id="planner")
            with self.assertRaises(ValueError):
                store.resolve_uncertain_execution(
                    lease.execution_id, outcome="succeeded", evidence_source="provider_api",
                    evidence_sha256="bad", provider_reference="request-123", resolver_id="operator-1",
                )
            self.assertEqual(store.list_executions(provider_id="local.engine")[0]["status"], "running")

    def test_completed_execution_is_not_reported_as_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderGovernanceStore(str(Path(directory) / "provider.sqlite3"))
            lease = store.acquire(provider_id="local.engine", agent_id="planner")
            self.assertTrue(store.record_outcome(
                lease.lease_id, outcome="succeeded", reason="provider_success"
            ))
            self.assertEqual(store.list_executions(provider_id="local.engine"), [])
            completed = store.list_executions(
                provider_id="local.engine", statuses=("succeeded",)
            )
            self.assertEqual(completed[0]["execution_id"], lease.execution_id)


if __name__ == "__main__":
    unittest.main()
