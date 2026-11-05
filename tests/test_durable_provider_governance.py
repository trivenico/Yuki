"""Cross-instance and crash-recovery tests for durable provider governance."""
import tempfile
import threading
import unittest
from pathlib import Path

from providers.durable_provider_governance import (
    DurableBudgetExceeded, DurableCircuitOpen, SQLiteProviderGovernanceStore,
)


class SQLiteProviderGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "provider-governance.sqlite3")
        self.now = [1000.0]
        self.store = self.make_store()

    def make_store(self, **overrides):
        settings = dict(
            max_global_concurrent=2, max_provider_concurrent=2,
            max_agent_concurrent=1, max_agent_requests_per_window=3,
            window_seconds=10, lease_timeout_seconds=5,
            failure_threshold=2, reset_timeout_seconds=10,
            clock=lambda: self.now[0],
        )
        settings.update(overrides)
        return SQLiteProviderGovernanceStore(self.path, **settings)

    def test_reservations_are_visible_across_store_instances(self):
        lease = self.store.acquire(provider_id="ollama", agent_id="planner")
        other = self.make_store()
        self.assertEqual(other.snapshot(provider_id="ollama", agent_id="planner")["global_active_requests"], 1)
        with self.assertRaisesRegex(DurableBudgetExceeded, "agent_concurrency"):
            other.acquire(provider_id="gemini", agent_id="planner")
        self.assertTrue(other.release(lease.lease_id))

    def test_atomic_concurrency_limit_across_threads_and_instances(self):
        stores = [self.make_store() for _ in range(8)]
        barrier = threading.Barrier(8)
        leases, rejected = [], []
        lock = threading.Lock()
        def attempt(index):
            barrier.wait()
            try:
                lease = stores[index].acquire(provider_id="ollama", agent_id="agent-"+str(index))
                with lock:
                    leases.append(lease)
            except DurableBudgetExceeded:
                with lock:
                    rejected.append(index)
        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(leases), 2)
        self.assertEqual(len(rejected), 6)

    def test_rate_window_persists_after_release_and_across_instances(self):
        for _ in range(3):
            lease = self.store.acquire(provider_id="ollama", agent_id="planner")
            self.store.release(lease.lease_id)
        with self.assertRaisesRegex(DurableBudgetExceeded, "request_rate"):
            self.make_store().acquire(provider_id="ollama", agent_id="planner")
        self.now[0] += 10
        lease = self.make_store().acquire(provider_id="ollama", agent_id="planner")
        self.assertTrue(self.store.release(lease.lease_id))

    def test_expired_worker_lease_recovered_by_next_admission(self):
        lease = self.store.acquire(provider_id="ollama", agent_id="planner")
        self.now[0] += 5
        replacement = self.make_store().acquire(provider_id="ollama", agent_id="planner")
        self.assertNotEqual(lease.lease_id, replacement.lease_id)
        self.assertTrue(any(x["event_type"] == "expired" for x in self.store.list_events(provider_id="ollama")))

    def test_failure_opens_circuit_across_instances(self):
        first = self.store.acquire(provider_id="ollama", agent_id="planner")
        self.store.record_outcome(first.lease_id, outcome="failed")
        second = self.store.acquire(provider_id="ollama", agent_id="planner")
        self.store.record_outcome(second.lease_id, outcome="failed")
        with self.assertRaises(DurableCircuitOpen):
            self.make_store().acquire(provider_id="ollama", agent_id="other")

    def test_only_half_open_probe_can_close_circuit(self):
        first = self.store.acquire(provider_id="ollama", agent_id="planner")
        second = self.store.acquire(provider_id="ollama", agent_id="other")
        self.store.record_outcome(first.lease_id, outcome="failed")
        self.store.record_outcome(second.lease_id, outcome="failed")
        self.now[0] += 10
        probe = self.make_store().acquire(provider_id="ollama", agent_id="probe")
        stale = self.make_store().acquire(provider_id="ollama", agent_id="stale")
        self.store.record_outcome(stale.lease_id, outcome="succeeded")
        self.assertEqual(self.store.snapshot(provider_id="ollama", agent_id="probe")["circuit_state"], "open")
        self.store.record_outcome(probe.lease_id, outcome="succeeded")
        self.assertEqual(self.store.snapshot(provider_id="ollama", agent_id="probe")["circuit_state"], "closed")

    def test_cancelled_probe_releases_probe_slot_without_health_penalty(self):
        a = self.store.acquire(provider_id="ollama", agent_id="a")
        b = self.store.acquire(provider_id="ollama", agent_id="b")
        self.store.record_outcome(a.lease_id, outcome="failed")
        self.store.record_outcome(b.lease_id, outcome="failed")
        self.now[0] += 10
        probe = self.store.acquire(provider_id="ollama", agent_id="probe")
        self.store.record_outcome(probe.lease_id, outcome="cancelled")
        next_probe = self.store.acquire(provider_id="ollama", agent_id="probe2")
        self.assertNotEqual(probe.lease_id, next_probe.lease_id)

    def test_audit_is_metadata_only_and_immutable(self):
        lease = self.store.acquire(provider_id="ollama", agent_id="planner")
        self.store.record_outcome(lease.lease_id, outcome="succeeded")
        events = self.store.list_events(provider_id="ollama")
        self.assertEqual([x["event_type"] for x in reversed(events)], ["admitted", "succeeded"])
        with self.store._connect() as db:
            with self.assertRaises(Exception):
                db.execute("UPDATE provider_governance_events SET reason='tampered' WHERE event_id=1")

    def test_stale_or_duplicate_outcome_cannot_mutate_state(self):
        lease = self.store.acquire(provider_id="ollama", agent_id="planner")
        self.assertTrue(self.store.release(lease.lease_id))
        self.assertFalse(self.store.record_outcome(lease.lease_id, outcome="failed"))

if __name__ == "__main__":
    unittest.main()
