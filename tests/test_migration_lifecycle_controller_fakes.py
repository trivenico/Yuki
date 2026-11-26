"""Tests for test-only migration audit and privileged identity fakes."""
from concurrent.futures import ThreadPoolExecutor
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
from test_migration_lifecycle_fakes_support import (
    InMemoryFencedMigrationRunner,
    InMemoryMigrationSecretManager,
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

    def test_target_runner_rejects_stale_fencing_generation(self):
        runner = InMemoryFencedMigrationRunner(
            clock=lambda: datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
        )
        digest = "a" * 64
        runner.execute(
            attempt_id="attempt-new", target_id="target-a",
            secret_reference="opaque://secret/new", manifest_digest=digest,
            fencing_generation=8,
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "stale target fencing"):
            runner.execute(
                attempt_id="attempt-stale", target_id="target-a",
                secret_reference="opaque://secret/stale", manifest_digest=digest,
                fencing_generation=7,
            )

    def test_idempotent_replay_advances_target_fencing_watermark(self):
        runner = InMemoryFencedMigrationRunner()
        digest = "c" * 64
        runner.execute(
            attempt_id="attempt-replayed", target_id="target-a",
            secret_reference="opaque://secret/1", manifest_digest=digest,
            fencing_generation=1,
        )
        runner.execute(
            attempt_id="attempt-replayed", target_id="target-a",
            secret_reference="opaque://secret/1", manifest_digest=digest,
            fencing_generation=3,
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "stale target fencing"):
            runner.execute(
                attempt_id="attempt-old", target_id="target-a",
                secret_reference="opaque://secret/old", manifest_digest=digest,
                fencing_generation=2,
            )

    def test_target_runner_rejects_manifest_change_on_idempotent_replay(self):
        runner = InMemoryFencedMigrationRunner()
        runner.execute(
            attempt_id="attempt-replay", target_id="target-a",
            secret_reference="opaque://secret/1", manifest_digest="a" * 64,
            fencing_generation=1,
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "manifest digest"):
            runner.execute(
                attempt_id="attempt-replay", target_id="target-a",
                secret_reference="opaque://secret/1", manifest_digest="b" * 64,
                fencing_generation=1,
            )

    def test_target_runner_rejects_reuse_of_consumed_generation_by_another_attempt(self):
        runner = InMemoryFencedMigrationRunner()
        runner.execute(
            attempt_id="attempt-first", target_id="target-a",
            secret_reference="opaque://secret/first", manifest_digest="a" * 64,
            fencing_generation=4,
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "already consumed"):
            runner.execute(
                attempt_id="attempt-second", target_id="target-a",
                secret_reference="opaque://secret/second", manifest_digest="b" * 64,
                fencing_generation=4,
            )

    def test_target_runner_rejects_attempt_replay_for_different_target(self):
        runner = InMemoryFencedMigrationRunner()
        runner.execute(
            attempt_id="attempt-bound", target_id="target-a",
            secret_reference="opaque://secret/1", manifest_digest="a" * 64,
            fencing_generation=1,
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "target identity"):
            runner.execute(
                attempt_id="attempt-bound", target_id="target-b",
                secret_reference="opaque://secret/1", manifest_digest="a" * 64,
                fencing_generation=1,
            )

    def test_target_runner_rejects_attempt_replay_for_different_secret_reference(self):
        runner = InMemoryFencedMigrationRunner()
        runner.execute(
            attempt_id="attempt-bound-secret", target_id="target-a",
            secret_reference="opaque://secret/1", manifest_digest="a" * 64,
            fencing_generation=1,
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "secret reference"):
            runner.execute(
                attempt_id="attempt-bound-secret", target_id="target-a",
                secret_reference="opaque://secret/2", manifest_digest="a" * 64,
                fencing_generation=1,
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

    def test_role_identifiers_reject_sql_fragments_and_invalid_names(self):
        admin = InMemoryMigrationIdentityAdmin()
        for role_name in ("bad-role", "role;drop", "A_role", "x" * 64, ""):
            with self.subTest(role_name=role_name):
                with self.assertRaisesRegex(MigrationAttemptConflict, "invalid PostgreSQL"):
                    admin.create_temporary_login(
                        attempt_id="attempt-invalid", role_name=role_name
                    )

    def test_owner_role_must_be_registered_as_no_login_non_escalating(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(
            attempt_id="attempt-owner-policy", role_name="yuki_migrate_policy"
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "not registered"):
            admin.grant_owner_role(
                attempt_id="attempt-owner-policy", login_role="yuki_migrate_policy",
                owner_role="unreviewed_owner",
            )
        admin._owner_roles["yuki_schema_owner"]["superuser"] = True
        with self.assertRaisesRegex(MigrationAttemptConflict, "NOLOGIN and non-escalating"):
            admin.grant_owner_role(
                attempt_id="attempt-owner-policy", login_role="yuki_migrate_policy",
                owner_role="yuki_schema_owner",
            )

    def test_temporary_login_rejects_any_existing_cluster_role(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.register_reserved_role("yuki_runtime_service")
        with self.assertRaisesRegex(MigrationAttemptConflict, "reserved service role"):
            admin.create_temporary_login(
                attempt_id="attempt-service-collision",
                role_name="yuki_runtime_service",
            )

    def test_role_cannot_be_registered_as_both_service_and_owner(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.register_reserved_role("yuki_shared_role")
        with self.assertRaisesRegex(MigrationAttemptConflict, "already registered"):
            admin.register_reserved_role("yuki_shared_role", owner=True)

    def test_role_cannot_be_registered_as_both_owner_and_service(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.register_reserved_role("yuki_shared_role", owner=True)
        with self.assertRaisesRegex(MigrationAttemptConflict, "already registered"):
            admin.register_reserved_role("yuki_shared_role")

    def test_concurrent_retry_for_same_attempt_and_role_is_idempotent(self):
        admin = InMemoryMigrationIdentityAdmin()

        def create():
            admin.create_temporary_login(
                attempt_id="attempt-same-retry", role_name="yuki_migrate_same"
            )
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: create(), range(2)))

        self.assertEqual(results, [True, True])
        self.assertEqual(
            admin._attempt_by_role["yuki_migrate_same"], "attempt-same-retry"
        )
        self.assertEqual(len(admin._roles), 1)

    def test_concurrent_attempts_cannot_claim_same_login_role_name(self):
        admin = InMemoryMigrationIdentityAdmin()

        def claim(attempt_id):
            try:
                admin.create_temporary_login(
                    attempt_id=attempt_id, role_name="yuki_migrate_race"
                )
                return "created"
            except MigrationAttemptConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ("attempt-race-a", "attempt-race-b")))

        self.assertCountEqual(results, ["created", "conflict"])
        self.assertEqual(
            admin._attempt_by_role["yuki_migrate_race"],
            next(
                attempt_id for attempt_id, result in zip(
                    ("attempt-race-a", "attempt-race-b"), results
                ) if result == "created"
            ),
        )

    def test_reserved_role_cannot_be_registered_after_migration_login_uses_name(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(
            attempt_id="attempt-reserve-collision",
            role_name="yuki_migrate_reserved",
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "already used"):
            admin.register_reserved_role("yuki_migrate_reserved")

    def test_temporary_login_cannot_collide_with_reserved_owner_role(self):
        admin = InMemoryMigrationIdentityAdmin()
        with self.assertRaisesRegex(MigrationAttemptConflict, "collides with a reserved owner role"):
            admin.create_temporary_login(
                attempt_id="attempt-role-collision", role_name="yuki_schema_owner"
            )

    def test_temporary_login_cannot_be_its_own_owner_role(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(
            attempt_id="attempt-self", role_name="yuki_migrate_self"
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "own owner role"):
            admin.grant_owner_role(
                attempt_id="attempt-self", login_role="yuki_migrate_self",
                owner_role="yuki_migrate_self",
            )

    def test_retirement_fails_if_any_unexpected_role_membership_remains(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(attempt_id="attempt-drift", role_name="yuki_migrate_drift")
        admin.grant_owner_role(
            attempt_id="attempt-drift", login_role="yuki_migrate_drift",
            owner_role="yuki_schema_owner",
        )
        admin.revoke_owner_role(
            attempt_id="attempt-drift", login_role="yuki_migrate_drift",
            owner_role="yuki_schema_owner",
        )
        admin.terminate_sessions(attempt_id="attempt-drift", login_role="yuki_migrate_drift")
        admin.disable_login(attempt_id="attempt-drift", login_role="yuki_migrate_drift")
        # Simulate out-of-band catalog drift that the retirement verifier must detect.
        admin._roles["attempt-drift"]["owner_roles"].add("unexpected_privileged_role")
        self.assertFalse(admin.verify_retired(
            attempt_id="attempt-drift", login_role="yuki_migrate_drift",
            owner_role="yuki_schema_owner",
        ))

    def test_revoke_rejects_owner_role_not_bound_to_attempt(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(
            attempt_id="attempt-revoke-binding", role_name="yuki_migrate_revoke"
        )
        admin.grant_owner_role(
            attempt_id="attempt-revoke-binding", login_role="yuki_migrate_revoke",
            owner_role="yuki_schema_owner",
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "different owner role"):
            admin.revoke_owner_role(
                attempt_id="attempt-revoke-binding", login_role="yuki_migrate_revoke",
                owner_role="another_schema_owner",
            )
        self.assertEqual(
            admin._roles["attempt-revoke-binding"]["owner_roles"],
            {"yuki_schema_owner"},
        )

    def test_retirement_verification_rejects_wrong_owner_role_binding(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(
            attempt_id="attempt-verify-binding", role_name="yuki_migrate_verify"
        )
        admin.grant_owner_role(
            attempt_id="attempt-verify-binding", login_role="yuki_migrate_verify",
            owner_role="yuki_schema_owner",
        )
        admin.revoke_owner_role(
            attempt_id="attempt-verify-binding", login_role="yuki_migrate_verify",
            owner_role="yuki_schema_owner",
        )
        admin.terminate_sessions(
            attempt_id="attempt-verify-binding", login_role="yuki_migrate_verify"
        )
        admin.disable_login(
            attempt_id="attempt-verify-binding", login_role="yuki_migrate_verify"
        )
        self.assertFalse(admin.verify_retired(
            attempt_id="attempt-verify-binding", login_role="yuki_migrate_verify",
            owner_role="another_schema_owner",
        ))
        self.assertTrue(admin.verify_retired(
            attempt_id="attempt-verify-binding", login_role="yuki_migrate_verify",
            owner_role="yuki_schema_owner",
        ))

    def test_attempt_cannot_receive_membership_in_multiple_owner_roles(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(attempt_id="attempt-owner", role_name="yuki_migrate_owner")
        admin.grant_owner_role(
            attempt_id="attempt-owner", login_role="yuki_migrate_owner",
            owner_role="yuki_schema_owner",
        )
        with self.assertRaisesRegex(MigrationAttemptConflict, "different owner role"):
            admin.grant_owner_role(
                attempt_id="attempt-owner", login_role="yuki_migrate_owner",
                owner_role="another_schema_owner",
            )
        self.assertEqual(
            admin._roles["attempt-owner"]["owner_roles"], {"yuki_schema_owner"}
        )

    def test_login_role_name_cannot_be_shared_across_attempts(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(attempt_id="attempt-1", role_name="yuki_migrate_shared")
        with self.assertRaisesRegex(MigrationAttemptConflict, "another attempt"):
            admin.create_temporary_login(
                attempt_id="attempt-2", role_name="yuki_migrate_shared"
            )
        with self.assertRaises(MigrationAttemptConflict):
            admin.grant_owner_role(
                attempt_id="attempt-2",
                login_role="yuki_migrate_shared",
                owner_role="yuki_schema_owner",
            )


    def test_reconciliation_resumes_after_crash_at_each_cleanup_boundary(self):
        for crash_after in range(5):
            with self.subTest(crash_after=crash_after):
                admin = InMemoryMigrationIdentityAdmin()
                secrets = InMemoryMigrationSecretManager()
                attempt_id = f"attempt-crash-{crash_after}"
                login_role = f"yuki_migrate_{crash_after}"
                owner_role = "yuki_schema_owner"
                admin.create_temporary_login(attempt_id=attempt_id, role_name=login_role)
                admin.grant_owner_role(
                    attempt_id=attempt_id, login_role=login_role, owner_role=owner_role
                )
                admin.add_session(
                    attempt_id=attempt_id, login_role=login_role, session_id="backend-1"
                )
                reference = secrets.issue(attempt_id=attempt_id, target_id="target-a")
                cleanup_steps = (
                    lambda: admin.revoke_owner_role(
                        attempt_id=attempt_id, login_role=login_role, owner_role=owner_role
                    ),
                    lambda: admin.terminate_sessions(attempt_id=attempt_id, login_role=login_role),
                    lambda: admin.disable_login(attempt_id=attempt_id, login_role=login_role),
                    lambda: secrets.revoke_attempt(
                        attempt_id=attempt_id, secret_reference=reference
                    ),
                )
                for step in cleanup_steps[:crash_after]:
                    step()
                for step in cleanup_steps:
                    step()
                self.assertTrue(admin.verify_retired(
                    attempt_id=attempt_id, login_role=login_role, owner_role=owner_role
                ))
                self.assertTrue(secrets.verify_retired(reference, attempt_id=attempt_id))

    def test_audit_readback_failure_is_visible_and_retryable(self):
        sink = InMemoryMigrationAuditSink()
        sink.append(self.event)
        sink.fail_next_verify = True
        with self.assertRaises(OSError):
            sink.verify_durable(self.event.event_id)
        self.assertTrue(sink.verify_durable(self.event.event_id))

    def test_repeated_reconciliation_does_not_restore_privileges(self):
        admin = InMemoryMigrationIdentityAdmin()
        admin.create_temporary_login(attempt_id="attempt-repeat", role_name="yuki_migrate_repeat")
        admin.grant_owner_role(
            attempt_id="attempt-repeat",
            login_role="yuki_migrate_repeat",
            owner_role="yuki_schema_owner",
        )
        admin.revoke_owner_role(
            attempt_id="attempt-repeat",
            login_role="yuki_migrate_repeat",
            owner_role="yuki_schema_owner",
        )
        admin.terminate_sessions(
            attempt_id="attempt-repeat", login_role="yuki_migrate_repeat"
        )
        admin.disable_login(attempt_id="attempt-repeat", login_role="yuki_migrate_repeat")
        for _ in range(3):
            admin.revoke_owner_role(
                attempt_id="attempt-repeat",
                login_role="yuki_migrate_repeat",
                owner_role="yuki_schema_owner",
            )
            admin.terminate_sessions(
                attempt_id="attempt-repeat", login_role="yuki_migrate_repeat"
            )
            admin.disable_login(attempt_id="attempt-repeat", login_role="yuki_migrate_repeat")
        self.assertTrue(admin.verify_retired(
            attempt_id="attempt-repeat",
            login_role="yuki_migrate_repeat",
            owner_role="yuki_schema_owner",
        ))

if __name__ == "__main__":
    unittest.main()
