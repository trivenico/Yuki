import os
import tempfile
import unittest

from core.workload_identity import WorkloadSigningKey
from core.workload_key_registry import SQLiteWorkloadKeyRegistry, WorkloadKeyRegistryError


class WorkloadKeyRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "keys.sqlite")
        self.registry = SQLiteWorkloadKeyRegistry(
            self.path, authorize_admin=lambda name: name == "security-admin",
            clock=lambda: 1700000000,
        )
        self.old = WorkloadSigningKey("yuki-prod", "key-old", "EdDSA", b"o" * 32)
        self.new = WorkloadSigningKey("yuki-prod", "key-new", "EdDSA", b"n" * 32)

    def tearDown(self):
        self.temp.cleanup()

    def test_staged_key_is_not_resolvable_until_activated(self):
        self.registry.register("security-admin", self.old)
        self.assertIsNone(self.registry.resolve("yuki-prod", "key-old", "EdDSA"))
        self.registry.activate("security-admin", "yuki-prod", "key-old")
        self.assertEqual(self.registry.resolve("yuki-prod", "key-old", "EdDSA"), self.old)

    def test_rotation_overlap_then_retirement(self):
        self.registry.register("security-admin", self.old)
        self.registry.activate("security-admin", "yuki-prod", "key-old")
        self.registry.register("security-admin", self.new)
        self.registry.activate("security-admin", "yuki-prod", "key-new")
        self.assertIsNotNone(self.registry.resolve("yuki-prod", "key-old", "EdDSA"))
        self.assertIsNotNone(self.registry.resolve("yuki-prod", "key-new", "EdDSA"))
        self.registry.retire("security-admin", "yuki-prod", "key-old")
        self.assertIsNone(self.registry.resolve("yuki-prod", "key-old", "EdDSA"))
        self.assertIsNotNone(self.registry.resolve("yuki-prod", "key-new", "EdDSA"))

    def test_cannot_retire_last_active_but_can_emergency_revoke(self):
        self.registry.register("security-admin", self.old)
        self.registry.activate("security-admin", "yuki-prod", "key-old")
        with self.assertRaises(WorkloadKeyRegistryError):
            self.registry.retire("security-admin", "yuki-prod", "key-old")
        self.registry.revoke("security-admin", "yuki-prod", "key-old")
        self.assertIsNone(self.registry.resolve("yuki-prod", "key-old", "EdDSA"))

    def test_revocation_is_terminal(self):
        self.registry.register("security-admin", self.old)
        self.registry.activate("security-admin", "yuki-prod", "key-old")
        self.registry.revoke("security-admin", "yuki-prod", "key-old")
        with self.assertRaises(WorkloadKeyRegistryError):
            self.registry.activate("security-admin", "yuki-prod", "key-old")

    def test_state_and_audit_survive_restart(self):
        self.registry.register("security-admin", self.old)
        self.registry.activate("security-admin", "yuki-prod", "key-old")
        restarted = SQLiteWorkloadKeyRegistry(self.path,
            authorize_admin=lambda name: True, clock=lambda: 1700000001)
        self.assertEqual(restarted.resolve("yuki-prod", "key-old", "EdDSA"), self.old)
        events = restarted.audit_events("security-admin")
        self.assertEqual([e["action"] for e in events], ["register", "active"])

    def test_admin_denial_and_unknown_algorithm_fail_closed(self):
        with self.assertRaises(WorkloadKeyRegistryError):
            self.registry.register("attacker", self.old)
        self.registry.register("security-admin", self.old)
        self.registry.activate("security-admin", "yuki-prod", "key-old")
        self.assertIsNone(self.registry.resolve("yuki-prod", "key-old", "RS256"))

    def test_duplicate_key_id_cannot_be_replaced(self):
        self.registry.register("security-admin", self.old)
        with self.assertRaises(WorkloadKeyRegistryError):
            self.registry.register("security-admin", WorkloadSigningKey(
                "yuki-prod", "key-old", "EdDSA", b"x" * 32))

    def test_private_key_material_is_not_accepted_or_persisted(self):
        self.registry.register("security-admin", self.old)
        with open(self.path, "rb") as handle:
            self.assertNotIn(b"PRIVATE KEY", handle.read())


if __name__ == "__main__":
    unittest.main()
