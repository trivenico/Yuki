"""Real PostgreSQL integration tests for shared workload key lifecycle state."""
import os
from pathlib import Path
import threading
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresWorkloadKeyRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.postgres_workload_key_registry import PostgresWorkloadKeyRegistry
        cls.registry_type = PostgresWorkloadKeyRegistry
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0004_workload_key_registry.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        self.issuer = "test-" + uuid.uuid4().hex
        self.admin = "security-admin"
        self.registry_a = self.registry_type(
            POSTGRES_URL, authorize_admin=lambda value: value == self.admin, max_pool_size=4
        )
        self.registry_b = self.registry_type(
            POSTGRES_URL, authorize_admin=lambda value: value == self.admin, max_pool_size=4
        )

    def tearDown(self):
        self.registry_a.close()
        self.registry_b.close()

    def key(self, key_id, material):
        from core.workload_identity import WorkloadSigningKey
        return WorkloadSigningKey(self.issuer, key_id, "EdDSA", material * 32)

    def test_register_activate_resolve_and_retire(self):
        self.assertIs(self.registry_a.check_ready(), True)
        old = self.key("old", b"o")
        new = self.key("new", b"n")
        self.registry_a.register(self.admin, old)
        self.assertIsNone(self.registry_b.resolve(self.issuer, "old", "EdDSA"))
        self.registry_b.activate(self.admin, self.issuer, "old")
        self.assertEqual(self.registry_a.resolve(self.issuer, "old", "EdDSA"), old)
        self.registry_a.register(self.admin, new)
        self.registry_a.activate(self.admin, self.issuer, "new")
        self.registry_b.retire(self.admin, self.issuer, "old")
        self.assertIsNone(self.registry_a.resolve(self.issuer, "old", "EdDSA"))
        self.assertEqual(self.registry_b.resolve(self.issuer, "new", "EdDSA"), new)

    def test_revocation_is_terminal_and_audit_is_durable(self):
        key = self.key("revoke-me", b"r")
        self.registry_a.register(self.admin, key)
        self.registry_a.activate(self.admin, self.issuer, key.key_id)
        self.registry_b.revoke(self.admin, self.issuer, key.key_id)
        self.assertIsNone(self.registry_a.resolve(self.issuer, key.key_id, "EdDSA"))
        from core.postgres_workload_key_registry import WorkloadKeyRegistryError
        with self.assertRaisesRegex(WorkloadKeyRegistryError, "key_lifecycle_terminal"):
            self.registry_a.activate(self.admin, self.issuer, key.key_id)
        actions = [row["action"] for row in self.registry_a.audit_events(self.admin, issuer=self.issuer)]
        self.assertEqual(actions, ["register", "active", "revoked"])

    def test_two_concurrent_retirements_cannot_remove_last_active_key(self):
        first, second = self.key("first", b"a"), self.key("second", b"b")
        for key in (first, second):
            self.registry_a.register(self.admin, key)
            self.registry_a.activate(self.admin, self.issuer, key.key_id)
        barrier = threading.Barrier(2)
        results, errors = [], []

        def retire(registry, key_id):
            try:
                barrier.wait(timeout=5)
                registry.retire(self.admin, self.issuer, key_id)
                results.append(key_id)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=retire, args=(self.registry_a, first.key_id)),
            threading.Thread(target=retire, args=(self.registry_b, second.key_id)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        with psycopg.connect(POSTGRES_URL) as conn:
            active = conn.execute(
                "SELECT count(*) FROM workload_keys WHERE issuer=%s AND state='active'",
                (self.issuer,),
            ).fetchone()[0]
        self.assertEqual(active, 1)

    def test_registration_validation_and_admin_fail_closed(self):
        from core.workload_identity import WorkloadSigningKey
        with self.assertRaises(PermissionError):
            self.registry_a.register("attacker", self.key("denied", b"d"))
        with self.assertRaises(ValueError):
            self.registry_a.register(
                self.admin, WorkloadSigningKey(self.issuer + "\n", "bad", "EdDSA", b"x" * 32)
            )
        self.registry_a.register(self.admin, self.key("valid", b"v"))
        from core.postgres_workload_key_registry import WorkloadKeyRegistryError
        with self.assertRaisesRegex(WorkloadKeyRegistryError, "key_id_already_registered"):
            self.registry_a.register(self.admin, self.key("valid", b"z"))

    def test_audit_rows_reject_update_and_delete(self):
        self.registry_a.register(self.admin, self.key("audit", b"a"))
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute(
                    "UPDATE workload_key_audit SET action='revoked' WHERE issuer=%s",
                    (self.issuer,),
                )
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute("DELETE FROM workload_key_audit WHERE issuer=%s", (self.issuer,))

    def test_audit_pagination_is_monotonic(self):
        self.registry_a.register(self.admin, self.key("page-a", b"a"))
        self.registry_a.register(self.admin, self.key("page-b", b"b"))
        first = self.registry_a.audit_events(self.admin, issuer=self.issuer, limit=1)
        second = self.registry_a.audit_events(self.admin, issuer=self.issuer, after_event_id=first[-1]["event_id"])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertGreater(second[0]["event_id"], first[0]["event_id"])

    def test_concurrent_audit_writes_remain_safe_for_cursor_pagination(self):
        barrier = threading.Barrier(2)
        errors = []

        def register(registry, prefix):
            try:
                barrier.wait(timeout=5)
                for index in range(8):
                    registry.register(self.admin, self.key(prefix + str(index), bytes([index + 1])))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=register, args=(self.registry_a, "left-")),
            threading.Thread(target=register, args=(self.registry_b, "right-")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(errors, [])
        cursor, seen = 0, []
        while True:
            page = self.registry_a.audit_events(self.admin, issuer=self.issuer, after_event_id=cursor, limit=3)
            if not page:
                break
            seen.extend(row["event_id"] for row in page)
            cursor = page[-1]["event_id"]
        self.assertEqual(len(seen), 16)
        self.assertEqual(seen, sorted(seen))
    def test_long_valid_issuer_is_shared_and_resolvable(self):
        issuer = "i" * 300
        from core.workload_identity import WorkloadSigningKey
        key = WorkloadSigningKey(issuer, "long", "EdDSA", b"l" * 32)
        self.registry_a.register(self.admin, key)
        self.registry_b.activate(self.admin, issuer, key.key_id)
        self.assertEqual(self.registry_a.resolve(issuer, key.key_id, "EdDSA"), key)


if __name__ == "__main__":
    unittest.main()
