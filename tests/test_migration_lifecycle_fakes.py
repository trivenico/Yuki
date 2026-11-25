"""Contract tests for test-only migration lifecycle fake adapters."""
from datetime import datetime, timedelta, timezone
import hashlib
import threading
import unittest

from core.migration_lifecycle import (
    MigrationAttempt,
    MigrationAttemptConflict,
    MigrationState,
)
from tests.test_migration_lifecycle_fakes_support import (
    InjectedStoreFailure,
    InMemoryMigrationAttemptStore,
    InMemoryTargetLeaseStore,
)


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class MigrationLifecycleFakeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.clock = MutableClock()
        self.store = InMemoryMigrationAttemptStore(clock=self.clock)
        self.digest = hashlib.sha256(b"approved manifest").hexdigest()

    def attempt(self, attempt_id="attempt-1", digest=None):
        return MigrationAttempt(
            attempt_id=attempt_id,
            target_id="postgres-primary",
            request_digest=digest or self.digest,
        )

    def test_create_is_idempotent_and_returns_original_snapshot(self):
        first = self.store.create(self.attempt(), idempotency_key="request-1")
        retry = self.store.create(
            self.attempt("different-attempt-id"), idempotency_key="request-1"
        )
        self.assertEqual(first, retry)
        self.assertEqual(retry.attempt_id, "attempt-1")
        self.assertEqual(retry.version, 0)

    def test_idempotency_key_digest_mismatch_conflicts(self):
        self.store.create(self.attempt(), idempotency_key="request-1")
        other_digest = hashlib.sha256(b"different manifest").hexdigest()
        with self.assertRaises(MigrationAttemptConflict):
            self.store.create(
                self.attempt("attempt-2", other_digest), idempotency_key="request-1"
            )

    def test_attempt_id_collision_with_different_key_conflicts(self):
        self.store.create(self.attempt(), idempotency_key="request-1")
        with self.assertRaises(MigrationAttemptConflict):
            self.store.create(self.attempt(), idempotency_key="request-2")

    def test_compare_and_swap_rejects_stale_version_without_mutation(self):
        created = self.store.create(self.attempt(), idempotency_key="request-1")
        advanced = self.store.transition(
            created.attempt_id, expected_version=0, new_state=MigrationState.AUTHORIZED
        )
        with self.assertRaises(MigrationAttemptConflict):
            self.store.transition(
                created.attempt_id, expected_version=0, new_state=MigrationState.IDENTITY_CREATED
            )
        self.assertEqual(self.store.get(created.attempt_id), advanced)

    def test_injected_persistence_failure_leaves_attempt_unchanged(self):
        created = self.store.create(self.attempt(), idempotency_key="request-1")
        self.store.fail_next_transition = True
        with self.assertRaises(InjectedStoreFailure):
            self.store.transition(
                created.attempt_id, expected_version=0, new_state=MigrationState.AUTHORIZED
            )
        self.assertEqual(self.store.get(created.attempt_id), created)
        retried = self.store.transition(
            created.attempt_id, expected_version=0, new_state=MigrationState.AUTHORIZED
        )
        self.assertEqual(retried.version, 1)

    def test_recovery_scan_is_bounded_ordered_and_excludes_completed(self):
        first = self.store.create(self.attempt("a"), idempotency_key="key-a")
        self.clock.advance(seconds=1)
        second = self.store.create(self.attempt("b"), idempotency_key="key-b")
        current = second
        for state in (
            MigrationState.AUTHORIZED, MigrationState.CLEANUP_PENDING,
            MigrationState.MEMBERSHIP_REVOKED, MigrationState.LOGIN_DISABLED,
            MigrationState.CREDENTIAL_REVOKED, MigrationState.RETIREMENT_VERIFIED,
            MigrationState.COMPLETED,
        ):
            current = self.store.transition(
                current.attempt_id, expected_version=current.version, new_state=state
            )
        results = self.store.list_recovery_candidates(before=self.clock(), limit=1)
        self.assertEqual([item.attempt_id for item in results], [first.attempt_id])
        self.assertEqual(results[0].state, MigrationState.REQUESTED)
        with self.assertRaises(ValueError):
            self.store.list_recovery_candidates(before=self.clock(), limit=0)

    def test_concurrent_idempotent_creates_converge_on_one_attempt(self):
        barrier = threading.Barrier(8)
        results = []
        errors = []

        def create(index):
            try:
                barrier.wait(timeout=3)
                result = self.store.create(
                    self.attempt(f"candidate-{index}"), idempotency_key="shared-key"
                )
                results.append(result.attempt_id)
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=create, args=(index,)) for index in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(len(set(results)), 1)

    def test_concurrent_cas_allows_only_one_transition(self):
        created = self.store.create(self.attempt(), idempotency_key="request-1")
        barrier = threading.Barrier(2)
        outcomes = []

        def advance(state):
            barrier.wait(timeout=3)
            try:
                outcomes.append(self.store.transition(
                    created.attempt_id, expected_version=0, new_state=state
                ).state)
            except MigrationAttemptConflict:
                outcomes.append("conflict")

        workers = [
            threading.Thread(target=advance, args=(MigrationState.AUTHORIZED,)),
            threading.Thread(target=advance, args=(MigrationState.AUTHORIZED,)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertCountEqual(outcomes, [MigrationState.AUTHORIZED, "conflict"])

    def test_expired_lease_is_replaced_with_higher_fencing_generation(self):
        leases = InMemoryTargetLeaseStore(clock=self.clock)
        first = leases.acquire("target-a", owner_id="worker-a", ttl_seconds=5)
        self.assertEqual(first.generation, 1)
        self.assertIsNone(leases.acquire("target-a", owner_id="worker-b", ttl_seconds=5))
        self.clock.advance(seconds=6)
        second = leases.acquire("target-a", owner_id="worker-b", ttl_seconds=5)
        self.assertEqual(second.generation, 2)
        with self.assertRaises(MigrationAttemptConflict):
            leases.renew(first, ttl_seconds=5)
        with self.assertRaises(MigrationAttemptConflict):
            leases.release(first)

    def test_lease_renewal_and_release_are_exact_owner_and_generation_bound(self):
        leases = InMemoryTargetLeaseStore(clock=self.clock)
        lease = leases.acquire("target-a", owner_id="worker-a", ttl_seconds=10)
        renewed = leases.renew(lease, ttl_seconds=20)
        self.assertEqual(renewed.generation, lease.generation)
        self.assertGreater(renewed.expires_at, lease.expires_at)
        with self.assertRaises(MigrationAttemptConflict):
            leases.release(lease)
        leases.release(renewed)
        next_lease = leases.acquire("target-a", owner_id="worker-b", ttl_seconds=10)
        self.assertEqual(next_lease.generation, 2)

    def test_expired_lease_cannot_be_renewed_or_released(self):
        leases = InMemoryTargetLeaseStore(clock=self.clock)
        lease = leases.acquire("target-a", owner_id="worker-a", ttl_seconds=1)
        self.clock.advance(seconds=2)
        with self.assertRaises(MigrationAttemptConflict):
            leases.renew(lease, ttl_seconds=5)
        with self.assertRaises(MigrationAttemptConflict):
            leases.release(lease)

    def test_invalid_lease_ttl_is_rejected(self):
        leases = InMemoryTargetLeaseStore(clock=self.clock)
        with self.assertRaises(ValueError):
            leases.acquire("target-a", owner_id="worker-a", ttl_seconds=True)
        with self.assertRaises(ValueError):
            leases.acquire("target-a", owner_id="worker-a", ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
