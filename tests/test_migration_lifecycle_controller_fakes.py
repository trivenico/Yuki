"""Tests for test-only migration audit and privileged identity fakes."""
from datetime import datetime, timezone
import unittest

from core.migration_lifecycle import (
    MigrationAttemptConflict,
    MigrationAuditEvent,
    MigrationState,
)
from test_migration_lifecycle_controller_fakes_support import (
    InMemoryMigrationAuditSink,
    InMemoryMigrationIdentityAdmin,
)


class MigrationControllerFakeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.event = MigrationAuditEvent(
            event_id="event-1",
            attempt_id="attempt-1",
            event_type="state_transition",
            actor_id="migration-controller",
            occurred_at=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
            from_state=MigrationState.REQUESTED,
            to_state=MigrationState.AUTHORIZED,
            detail_code="approval_recorded",
        )

    def test_audit_append_is_idempotent_and_readback_verifiable(self):
        sink = InMemoryMigrationAuditSink()
        sink.append(self.event)
        sink.append(self.event)
        self.assertTrue(sink.verify_durable(self.event.event_id))
        self.assertEqual(sink.get(self.event.event_id), self.event)

    def test_audit_event_id_collision_with_different_content_conflicts(self):
        sink = InMemoryMigrationAuditSink()
        sink.append(self.event)
        changed = MigrationAuditEvent(
            event_id=self.event.event_id,
            attempt_id=self.event.attempt_id,
            event_type=self.event.event_type,
            actor_id="different-actor",
            occurred_at=self.event.occurred_at,
            from_state=self.event.from_state,
            to_state=self.event.to_state,
            detail_code=self.event.detail_code,
        )
        with self.assertRaises(MigrationAttemptConflict):
            sink.append(changed)

    def test_audit_append_failure_is_not_reported_as_durable(self):
        sink = InMemoryMigrationAuditSink()
        sink.fail_next_append = True
        with self.assertRaises(OSError):
            sink.append(self.event)
        self.assertFalse(sink.verify_durable(self.event.event_id))
        sink.append(self.event)
        self.assertTrue(sink.verify_durable(self.event.event_id))

    def test_independent_audit_readback_failure_is_visible_and_retryable(self):
        sink = InMemoryMigrationAuditSink()
        sink.append(self.event)
        sink.fail_next_verify = True
        with self.assertRaises(OSError):
            sink.verify_durable(self.event.event_id)
        self.assertTrue(sink.verify_durable(self.event.event_id))

    def test_identity_admin_operations_are_idempotent(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(attempt_id="attempt-1", role_name="yuki_migrate_1")
        admin.create_temporary_login(attempt_id="attempt-1", role_name="yuki_migrate_1")
        admin.grant_owner_role(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        )
        admin.grant_owner_role(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        )
        admin.revoke_owner_role(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        )
        admin.revoke_owner_role(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        )
        admin.terminate_sessions(attempt_id="attempt-1", login_role="yuki_migrate_1")
        admin.disable_login(attempt_id="attempt-1", login_role="yuki_migrate_1")
        admin.disable_login(attempt_id="attempt-1", login_role="yuki_migrate_1")
        self.assertTrue(admin.verify_retired(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        ))

    def test_retirement_requires_membership_revocation_login_disable_and_no_sessions(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(attempt_id="attempt-1", role_name="yuki_migrate_1")
        admin.grant_owner_role(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        )
        admin.add_session(
            attempt_id="attempt-1", login_role="yuki_migrate_1", session_id="backend-42"
        )
        self.assertFalse(admin.verify_retired(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        ))
        admin.revoke_owner_role(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        )
        self.assertFalse(admin.verify_retired(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        ))
        admin.terminate_sessions(attempt_id="attempt-1", login_role="yuki_migrate_1")
        admin.disable_login(attempt_id="attempt-1", login_role="yuki_migrate_1")
        self.assertTrue(admin.verify_retired(
            attempt_id="attempt-1", login_role="yuki_migrate_1", owner_role="yuki_schema_owner"
        ))

    def test_identity_attempt_cannot_be_rebound_to_another_role_name(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(attempt_id="attempt-1", role_name="yuki_migrate_1")
        with self.assertRaises(MigrationAttemptConflict):
            admin.create_temporary_login(attempt_id="attempt-1", role_name="yuki_migrate_2")
        with self.assertRaises(MigrationAttemptConflict):
            admin.grant_owner_role(
                attempt_id="attempt-1", login_role="yuki_migrate_2", owner_role="yuki_schema_owner"
            )


if __name__ == "__main__":
    unittest.main()
