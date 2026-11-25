"""Cross-connection integration tests for the shared specialist registry."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresSpecialistAgentRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
        from automation.postgres_specialist_agent_registry import PostgresSpecialistAgentRegistry

        cls.agent_type = SpecialistAgent
        cls.registry_type = PostgresSpecialistAgentRegistry
        cls.validate_agent = SpecialistAgentCoordinator._validate_agent
        root = Path(__file__).resolve().parents[1]
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                (root / "migrations/security/0017_specialist_agent_registry.sql").read_text(encoding="utf-8"),
                prepare=False,
            )

    def setUp(self):
        self.agent_id = "test-" + uuid.uuid4().hex[:24]
        self.first = self.registry_type(
            POSTGRES_URL, agent_factory=self.agent_type, validate_agent=self.validate_agent,
            min_pool_size=1, max_pool_size=2,
        )
        self.second = self.registry_type(
            POSTGRES_URL, agent_factory=self.agent_type, validate_agent=self.validate_agent,
            min_pool_size=1, max_pool_size=2,
        )
        self.addCleanup(self.first.close)
        self.addCleanup(self.second.close)

    def agent(self, *, name="Registry test agent", enabled=True):
        return self.agent_type(
            self.agent_id, name, frozenset({"registry-test"}), "emit",
            frozenset({"registry:read"}), enabled,
        )

    def test_registry_exposes_production_readiness_and_non_secret_identity(self):
        self.assertIs(self.first.check_ready(), True)
        self.assertTrue(self.first.database_identity.startswith("postgres-endpoint:"))
        self.assertEqual(self.first.database_identity, self.second.database_identity)

    def test_shared_visibility_and_manifest_revision_are_atomic(self):
        self.first.initialize((self.agent(),))
        self.assertIn(self.agent_id, {agent.agent_id for agent in self.second.list_agents()})
        self.assertEqual(self.first.get_manifest_revision(self.agent_id), 1)

        disabled = self.second.set_agent_enabled(self.agent_id, False)
        self.assertFalse(disabled.enabled)
        self.assertEqual(self.first.get_manifest_revision(self.agent_id), 2)

        # Repeating the same state is idempotent and does not manufacture a revision.
        self.first.set_agent_enabled(self.agent_id, False)
        self.assertEqual(self.second.get_manifest_revision(self.agent_id), 2)

        enabled = self.first.set_agent_enabled(self.agent_id, True)
        self.assertTrue(enabled.enabled)
        self.assertEqual(self.second.get_manifest_revision(self.agent_id), 3)

    def test_concurrent_enable_changes_serialize_across_independent_pools(self):
        self.first.initialize((self.agent(),))
        barrier = Barrier(2)

        def change(registry, enabled):
            barrier.wait(timeout=5)
            return registry.set_agent_enabled(self.agent_id, enabled)

        with ThreadPoolExecutor(max_workers=2) as executor:
            left = executor.submit(change, self.first, False)
            right = executor.submit(change, self.second, True)
            results = (left.result(timeout=15), right.result(timeout=15))

        self.assertEqual(len(results), 2)
        revision = self.first.get_manifest_revision(self.agent_id)
        # At least one actual transition must have occurred; the second may
        # become an idempotent no-op depending on lock acquisition order.
        self.assertIn(revision, (2, 3))
        visible = {
            agent.agent_id: agent for agent in self.second.list_agents()
        }[self.agent_id]
        self.assertIsInstance(visible.enabled, bool)

    def test_initialize_never_overwrites_existing_operator_manifest(self):
        original = self.agent(name="Operator-owned definition")
        self.first.initialize((original,))
        self.second.initialize((self.agent(name="Stale bundled default"),))
        visible = {agent.agent_id: agent for agent in self.first.list_agents()}
        self.assertEqual(visible[self.agent_id].name, "Operator-owned definition")

    def test_duplicate_registration_is_rejected(self):
        self.first.register_agent(self.agent())
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.second.register_agent(self.agent(name="Duplicate"))

    def test_enable_change_for_unknown_agent_fails_closed(self):
        with self.assertRaises(KeyError):
            self.first.set_agent_enabled(self.agent_id, False)

    def test_manifest_validation_rejects_invalid_agent_before_write(self):
        invalid = self.agent()
        object.__setattr__(invalid, "action", "execute")
        with self.assertRaises(ValueError):
            self.first.register_agent(invalid)
        self.assertNotIn(self.agent_id, {agent.agent_id for agent in self.first.list_agents()})


if __name__ == "__main__":
    unittest.main()
