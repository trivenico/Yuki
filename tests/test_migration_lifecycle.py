"""Unit tests for the provider-neutral migration lifecycle domain contract."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from core.migration_lifecycle import (
    InvalidMigrationTransition,
    MigrationAuditEvent,
    MigrationAttempt,
    MigrationExecutionResult,
    MigrationAttemptConflict,
    MigrationState,
    TargetLease,
    transition_attempt,
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
        self.assertEqual(current.state, MigrationState.MEMBERSHIP_REVOKED)

    def test_stale_version_and_illegal_skips_are_rejected(self):
        with self.assertRaises(MigrationAttemptConflict):
            transition_attempt(
                self.attempt, MigrationState.AUTHORIZED, expected_version=4, now=self.now
            )
        with self.assertRaises(InvalidMigrationTransition):
            self.advance(self.attempt, MigrationState.MIGRATION_RUNNING)

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

    def test_target_lease_requires_positive_fence_and_aware_expiry(self):
        lease = TargetLease("target", "worker", 1, self.now + timedelta(minutes=1))
        self.assertEqual(lease.generation, 1)
        with self.assertRaises(ValueError):
            TargetLease("target", "worker", 0, self.now)
        with self.assertRaises(ValueError):
            TargetLease("target", "worker", 1, datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
