"""Unit tests for the provider-neutral migration lifecycle domain contract."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
from concurrent.futures import ThreadPoolExecutor

from core.migration_lifecycle import (
    MigrationAuditEvent,
    MigrationAttempt,
    MigrationExecutionResult,
    MigrationCredentialBinding,
    CredentialProvisioningRecord,
    CredentialProvisioningState,
    InvalidMigrationTransition,
    transition_credential_record,
    MigrationAttemptConflict,
    MigrationState,
    TargetLease,
    transition_attempt,
    validate_new_attempt,
)


from tests.test_migration_lifecycle_fakes_support import (
    InMemoryCredentialProvisioningStore,
    InjectedStoreFailure,
)


class MigrationLifecycleDomainTests(unittest.TestCase):

    def setUp(self):
        self.now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
        self.attempt = MigrationAttempt(
            attempt_id="attempt-1",
            target_id="postgres-primary",
            request_digest=hashlib.sha256(b"approved migration manifest").hexdigest(),
            created_at=self.now,
            updated_at=self.now,
        )

    def advance(self, attempt, state, **kwargs):
        return transition_attempt(
            attempt, state, expected_version=attempt.version, now=self.now + timedelta(seconds=attempt.version + 1),
            **kwargs,
        )

    def test_happy_path_requires_secret_reference_and_verified_retirement(self):
        current = self.attempt
        for state in (
            MigrationState.AUTHORIZED,
            MigrationState.IDENTITY_CREATED,
            MigrationState.MEMBERSHIP_GRANTED,
        ):
            current = self.advance(current, state)
        with self.assertRaises(ValueError):
            self.advance(current, MigrationState.CREDENTIAL_ISSUED)
        current = self.advance(
            current, MigrationState.CREDENTIAL_ISSUED, secret_reference="vault://secret/version/7"
        )
        for state in (
            MigrationState.MIGRATION_RUNNING,
            MigrationState.MIGRATION_SUCCEEDED,
            MigrationState.CLEANUP_PENDING,
            MigrationState.MEMBERSHIP_REVOKED,
            MigrationState.LOGIN_DISABLED,
            MigrationState.CREDENTIAL_REVOKED,
            MigrationState.RETIREMENT_VERIFIED,
            MigrationState.COMPLETED,
        ):
            current = self.advance(current, state)
        self.assertEqual(current.state, MigrationState.COMPLETED)
        self.assertEqual(current.version, 12)
        self.assertEqual(current.secret_reference, "vault://secret/version/7")

    def test_failure_path_must_enter_cleanup_and_cannot_skip_retirement(self):
        current = self.attempt
        for state in (
            MigrationState.AUTHORIZED, MigrationState.IDENTITY_CREATED,
            MigrationState.MEMBERSHIP_GRANTED,
        ):
            current = self.advance(current, state)
        current = self.advance(
            current, MigrationState.CREDENTIAL_ISSUED, secret_reference="secret-ref-2"
        )
        current = self.advance(current, MigrationState.MIGRATION_RUNNING)
        current = self.advance(current, MigrationState.MIGRATION_FAILED)
        with self.assertRaises(InvalidMigrationTransition):
            self.advance(current, MigrationState.COMPLETED)
        current = self.advance(current, MigrationState.CLEANUP_PENDING)
        self.assertEqual(current.state, MigrationState.CLEANUP_PENDING)

    def test_cleanup_can_begin_before_identity_or_credential_creation(self):
        current = self.advance(self.attempt, MigrationState.AUTHORIZED)
        current = self.advance(current, MigrationState.CLEANUP_PENDING)
        self.assertEqual(current.state, MigrationState.CLEANUP_PENDING)
        current = self.advance(current, MigrationState.MEMBERSHIP_REVOKED)
        current = self.advance(current, MigrationState.LOGIN_DISABLED)
        current = self.advance(current, MigrationState.CREDENTIAL_REVOKED)
        current = self.advance(current, MigrationState.RETIREMENT_VERIFIED)
        current = self.advance(current, MigrationState.COMPLETED)
        self.assertEqual(current.state, MigrationState.COMPLETED)
        self.assertIsNone(current.secret_reference)

    def test_stale_version_and_illegal_skips_are_rejected(self):
        with self.assertRaises(MigrationAttemptConflict):
            transition_attempt(
                self.attempt, MigrationState.AUTHORIZED, expected_version=4, now=self.now
            )
        with self.assertRaises(InvalidMigrationTransition):
            self.advance(self.attempt, MigrationState.MIGRATION_RUNNING)

    def test_new_attempt_validation_enforces_initial_state_and_idempotency_key(self):
        self.assertEqual(
            validate_new_attempt(self.attempt, idempotency_key=" request-1 "),
            "request-1",
        )
        with self.assertRaises(ValueError):
            validate_new_attempt(self.attempt, idempotency_key=" ")
        authorized = self.advance(self.attempt, MigrationState.AUTHORIZED)
        with self.assertRaises(ValueError):
            validate_new_attempt(authorized, idempotency_key="request-2")
        issued = self.attempt
        for state in (
            MigrationState.AUTHORIZED, MigrationState.IDENTITY_CREATED,
            MigrationState.MEMBERSHIP_GRANTED,
        ):
            issued = self.advance(issued, state)
        issued = self.advance(
            issued, MigrationState.CREDENTIAL_ISSUED, secret_reference="vault://issued"
        )
        with self.assertRaises(ValueError):
            validate_new_attempt(issued, idempotency_key="request-3")

    def test_attempt_cannot_be_created_with_a_secret_reference_before_issuance(self):
        for state in (
            MigrationState.REQUESTED,
            MigrationState.AUTHORIZED,
            MigrationState.IDENTITY_CREATED,
            MigrationState.MEMBERSHIP_GRANTED,
        ):
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    MigrationAttempt(
                        attempt_id="premature-secret",
                        target_id="postgres-primary",
                        request_digest=self.attempt.request_digest,
                        state=state,
                        secret_reference="vault://secret/version/1",
                    )

    def test_secret_reference_cannot_be_recorded_before_issuance(self):
        current = self.advance(self.attempt, MigrationState.AUTHORIZED)
        with self.assertRaises(ValueError):
            self.advance(current, MigrationState.IDENTITY_CREATED, secret_reference="premature-ref")

    def test_secret_reference_cannot_be_changed_after_issuance(self):
        current = self.attempt
        for state in (
            MigrationState.AUTHORIZED, MigrationState.IDENTITY_CREATED,
            MigrationState.MEMBERSHIP_GRANTED,
        ):
            current = self.advance(current, state)
        current = self.advance(
            current, MigrationState.CREDENTIAL_ISSUED, secret_reference="secret-ref-a"
        )
        with self.assertRaises(MigrationAttemptConflict):
            self.advance(current, MigrationState.MIGRATION_RUNNING, secret_reference="secret-ref-b")

    def test_attempt_is_immutable_and_contains_no_secret_value_field(self):
        with self.assertRaises(FrozenInstanceError):
            self.attempt.state = MigrationState.AUTHORIZED
        self.assertFalse(hasattr(self.attempt, "password"))
        self.assertFalse(hasattr(self.attempt, "credential"))
        self.assertFalse(hasattr(self.attempt, "dsn"))

    def test_attempt_rejects_invalid_digest_identifiers_and_versions(self):
        for digest in ("", "A" * 64, "g" * 64, "0" * 63):
            with self.subTest(digest=digest):
                with self.assertRaises(ValueError):
                    MigrationAttempt("a", "target", digest)
        for kwargs in (
            {"attempt_id": " ", "target_id": "target"},
            {"attempt_id": "attempt", "target_id": " "},
            {"attempt_id": "attempt", "target_id": "target", "version": True},
            {"attempt_id": "attempt", "target_id": "target", "version": -1},
        ):
            values = {
                "attempt_id": "attempt", "target_id": "target",
                "request_digest": self.attempt.request_digest,
            }
            values.update(kwargs)
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    MigrationAttempt(**values)

    def test_attempt_rejects_naive_or_reversed_timestamps(self):
        with self.assertRaises(ValueError):
            MigrationAttempt(
                "a", "target", self.attempt.request_digest,
                created_at=datetime(2026, 1, 1),
            )
        with self.assertRaises(ValueError):
            MigrationAttempt(
                "a", "target", self.attempt.request_digest,
                created_at=self.now, updated_at=self.now - timedelta(seconds=1),
            )

    def test_runner_result_is_sanitized_and_validated(self):
        result = MigrationExecutionResult(
            manifest_digest=self.attempt.request_digest,
            applied_migrations=16,
            completed_at=self.now,
        )
        self.assertEqual(result.applied_migrations, 16)
        self.assertFalse(hasattr(result, "password"))
        with self.assertRaises(ValueError):
            MigrationExecutionResult("not-a-digest", 1, self.now)
        with self.assertRaises(ValueError):
            MigrationExecutionResult(self.attempt.request_digest, True, self.now)

    def test_audit_event_requires_stable_ids_and_typed_states(self):
        event = MigrationAuditEvent(
            event_id="event-1",
            attempt_id=self.attempt.attempt_id,
            event_type="state_transition",
            actor_id="migration-controller",
            occurred_at=self.now,
            from_state=MigrationState.REQUESTED,
            to_state=MigrationState.AUTHORIZED,
            detail_code="approval_recorded",
        )
        self.assertEqual(event.to_state, MigrationState.AUTHORIZED)
        self.assertFalse(hasattr(event, "secret"))
        with self.assertRaises(ValueError):
            MigrationAuditEvent(
                event_id="event-2", attempt_id="attempt-1", event_type="transition",
                actor_id="controller", occurred_at=self.now, from_state=None,
                to_state=MigrationState.AUTHORIZED, detail_code="password=plaintext",
            )

    def test_audit_event_rejects_impossible_state_transition(self):
        with self.assertRaisesRegex(ValueError, "forbidden transition"):
            MigrationAuditEvent(
                event_id="event-invalid",
                attempt_id=self.attempt.attempt_id,
                event_type="state_transition",
                actor_id="migration-controller",
                occurred_at=self.now,
                from_state=MigrationState.REQUESTED,
                to_state=MigrationState.MIGRATION_RUNNING,
            )

    def test_initial_audit_event_must_enter_requested(self):
        with self.assertRaisesRegex(ValueError, "initial audit event"):
            MigrationAuditEvent(
                event_id="event-invalid-initial",
                attempt_id=self.attempt.attempt_id,
                event_type="state_transition",
                actor_id="migration-controller",
                occurred_at=self.now,
                from_state=None,
                to_state=MigrationState.AUTHORIZED,
            )

    def test_audit_event_accepts_initial_requested_transition(self):
        event = MigrationAuditEvent(
            event_id="event-created",
            attempt_id=self.attempt.attempt_id,
            event_type="state_transition",
            actor_id="migration-controller",
            occurred_at=self.now,
            from_state=None,
            to_state=MigrationState.REQUESTED,
        )
        self.assertEqual(event.to_state, MigrationState.REQUESTED)

    def test_credential_binding_is_immutable_and_secret_free(self):
        binding = MigrationCredentialBinding(
            attempt_id="attempt-1",
            target_id="postgres-primary",
            login_role="yuki_release_abc123",
            secret_reference="vault://migration/credential/attempt-1",
            credential_version="version-7",
            bound_at=self.now,
        )
        self.assertEqual(binding.attempt_id, "attempt-1")
        self.assertEqual(binding.login_role, "yuki_release_abc123")
        self.assertFalse(hasattr(binding, "password"))
        self.assertFalse(hasattr(binding, "secret_value"))
        with self.assertRaises(FrozenInstanceError):
            binding.login_role = "other_role"

    def test_credential_binding_rejects_malformed_role_and_reference(self):
        base = {
            "attempt_id": "attempt-1",
            "target_id": "postgres-primary",
            "login_role": "yuki_release_abc123",
            "secret_reference": "vault://migration/credential/attempt-1",
            "credential_version": "version-7",
            "bound_at": self.now,
        }
        for role in ("UpperCase", "role-with-dash", "role;DROP", "a" * 64, ""):
            with self.subTest(role=role), self.assertRaises(ValueError):
                MigrationCredentialBinding(**{**base, "login_role": role})
        for reference in ("", "  ", "x" * 1025, "vault://bad\x00ref"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                MigrationCredentialBinding(**{**base, "secret_reference": reference})

    def test_credential_binding_requires_timezone_aware_timestamp(self):
        with self.assertRaises(ValueError):
            MigrationCredentialBinding(
                attempt_id="attempt-1",
                target_id="postgres-primary",
                login_role="yuki_release_abc123",
                secret_reference="vault://migration/credential/attempt-1",
                credential_version="version-7",
                bound_at=datetime(2026, 11, 25, 12, 0),
            )

    def test_credential_saga_records_each_irreversible_step_without_secret_bytes(self):
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1",
            target_id="postgres-primary",
            login_role="yuki_release_abc123",
            created_at=self.now,
        )
        record = transition_credential_record(
            record, CredentialProvisioningState.SECRET_ISSUED,
            expected_version=0, now=self.now + timedelta(seconds=1),
            secret_reference="vault://credential/attempt-1",
            credential_version="version-7",
        )
        record = transition_credential_record(
            record, CredentialProvisioningState.CREDENTIAL_APPLIED,
            expected_version=1, now=self.now + timedelta(seconds=2),
        )
        record = transition_credential_record(
            record, CredentialProvisioningState.VERIFIED,
            expected_version=2, now=self.now + timedelta(seconds=3),
        )
        self.assertEqual(record.state, CredentialProvisioningState.VERIFIED)
        self.assertEqual(record.secret_reference, "vault://credential/attempt-1")
        self.assertEqual(record.credential_version, "version-7")
        self.assertFalse(hasattr(record, "password"))
        self.assertFalse(hasattr(record, "secret_value"))

    def test_credential_saga_rejects_skips_and_stale_versions(self):
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        with self.assertRaises(InvalidMigrationTransition):
            transition_credential_record(
                record, CredentialProvisioningState.VERIFIED,
                expected_version=0, now=self.now,
            )
        with self.assertRaises(MigrationAttemptConflict):
            transition_credential_record(
                record, CredentialProvisioningState.SECRET_ISSUED,
                expected_version=1, now=self.now,
                secret_reference="vault://credential/attempt-1",
                credential_version="version-7",
            )

    def test_credential_saga_requires_reference_and_version_together(self):
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        for kwargs in (
            {"secret_reference": "vault://credential/attempt-1"},
            {"credential_version": "version-7"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                transition_credential_record(
                    record, CredentialProvisioningState.SECRET_ISSUED,
                    expected_version=0, now=self.now, **kwargs,
                )

    def test_credential_saga_allows_cleanup_after_each_partial_issue_boundary(self):
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        record = transition_credential_record(
            record, CredentialProvisioningState.RETIRING,
            expected_version=0, now=self.now + timedelta(seconds=1),
        )
        record = transition_credential_record(
            record, CredentialProvisioningState.RETIRED,
            expected_version=1, now=self.now + timedelta(seconds=2),
        )
        self.assertEqual(record.state, CredentialProvisioningState.RETIRED)
        self.assertIsNone(record.secret_reference)

        issued = CredentialProvisioningRecord(
            attempt_id="attempt-2", target_id="postgres-primary",
            login_role="yuki_release_def456", created_at=self.now,
        )
        issued = transition_credential_record(
            issued, CredentialProvisioningState.SECRET_ISSUED,
            expected_version=0, now=self.now + timedelta(seconds=1),
            secret_reference="vault://credential/attempt-2",
            credential_version="version-8",
        )
        issued = transition_credential_record(
            issued, CredentialProvisioningState.RETIRING,
            expected_version=1, now=self.now + timedelta(seconds=2),
        )
        issued = transition_credential_record(
            issued, CredentialProvisioningState.RETIRED,
            expected_version=2, now=self.now + timedelta(seconds=3),
        )
        self.assertEqual(issued.state, CredentialProvisioningState.RETIRED)
        self.assertEqual(issued.secret_reference, "vault://credential/attempt-2")

    def test_credential_saga_cannot_change_issued_secret_version(self):
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        record = transition_credential_record(
            record, CredentialProvisioningState.SECRET_ISSUED,
            expected_version=0, now=self.now,
            secret_reference="vault://credential/attempt-1",
            credential_version="version-7",
        )
        with self.assertRaises(MigrationAttemptConflict):
            transition_credential_record(
                record, CredentialProvisioningState.CREDENTIAL_APPLIED,
                expected_version=1, now=self.now + timedelta(seconds=1),
                secret_reference="vault://credential/attempt-1",
                credential_version="version-8",
            )

    def test_credential_store_create_is_idempotent_but_binding_is_immutable(self):
        store = InMemoryCredentialProvisioningStore()
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        self.assertEqual(store.create(record), record)
        self.assertEqual(store.create(record), record)
        conflict = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-secondary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "different target or LOGIN"):
            store.create(conflict)

    def test_credential_store_create_rejects_nonfresh_record(self):
        store = InMemoryCredentialProvisioningStore()
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123",
            state=CredentialProvisioningState.RETIRING,
            created_at=self.now,
        )
        with self.assertRaisesRegex(ValueError, "fresh reserved record"):
            store.create(record)

    def test_credential_store_compare_and_swap_allows_only_one_concurrent_transition(self):
        store = self.InMemoryCredentialProvisioningStore()
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        store.create(record)

        def advance():
            try:
                return store.transition(
                    "attempt-1", expected_version=0,
                    new_state=CredentialProvisioningState.RETIRING,
                )
            except MigrationAttemptConflict as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: advance(), range(2)))
        self.assertEqual(sum(isinstance(item, CredentialProvisioningRecord) for item in results), 1)
        self.assertEqual(sum(isinstance(item, MigrationAttemptConflict) for item in results), 1)
        self.assertEqual(store.get("attempt-1").version, 1)

    def test_credential_store_failure_does_not_partially_commit_transition(self):
        store = self.InMemoryCredentialProvisioningStore()
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        store.create(record)
        store.fail_next_transition = True
        with self.assertRaises(InjectedStoreFailure):
            store.transition(
                "attempt-1", expected_version=0,
                new_state=CredentialProvisioningState.RETIRING,
            )
        self.assertEqual(store.get("attempt-1"), record)
        self.assertEqual(store.get("attempt-1").version, 0)

    def test_credential_store_rejects_stale_transition_without_mutation(self):
        store = self.InMemoryCredentialProvisioningStore()
        record = CredentialProvisioningRecord(
            attempt_id="attempt-1", target_id="postgres-primary",
            login_role="yuki_release_abc123", created_at=self.now,
        )
        store.create(record)
        updated = store.transition(
            "attempt-1", expected_version=0,
            new_state=CredentialProvisioningState.RETIRING,
        )
        with self.assertRaises(MigrationAttemptConflict):
            store.transition(
                "attempt-1", expected_version=0,
                new_state=CredentialProvisioningState.RETIRED,
            )
        self.assertEqual(store.get("attempt-1"), updated)

    def test_target_lease_requires_positive_fence_and_aware_expiry(self):
        lease = TargetLease("target", "worker", 1, self.now + timedelta(minutes=1))
        self.assertEqual(lease.generation, 1)
        with self.assertRaises(ValueError):
            TargetLease("target", "worker", 0, self.now)
        with self.assertRaises(ValueError):
            TargetLease("target", "worker", 1, datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
