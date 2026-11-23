import tempfile
import unittest
from pathlib import Path

from automation.agent_execution_spec import AgentExecutionSpec
from core.execution_spec_registry import ExecutionSpecRegistry


class ExecutionSpecRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "specs.sqlite3")
        self.allowed = True
        self.registry = ExecutionSpecRegistry(
            self.path, authorize=lambda actor, owner, action: self.allowed and actor == owner
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def spec(version=1, instructions="Review the approved change.") -> AgentExecutionSpec:
        return AgentExecutionSpec(
            spec_id="review", role="reviewer", instructions=instructions,
            capabilities=("coding",), version=version,
        )

    def test_persists_and_reads_immutable_versions(self):
        first = self.spec()
        digest1 = self.registry.put(actor_id="owner-a", owner_id="owner-a", spec=first)
        second = self.spec(2, "Review the approved patch and tests.")
        digest2 = self.registry.put(actor_id="owner-a", owner_id="owner-a", spec=second)
        self.assertNotEqual(digest1, digest2)
        self.assertEqual(self.registry.get(actor_id="owner-a", owner_id="owner-a", spec_id="review").version, 2)
        self.assertEqual(self.registry.get(actor_id="owner-a", owner_id="owner-a", spec_id="review", version=1), first)

    def test_rejects_skipped_or_replayed_versions(self):
        with self.assertRaises(ValueError):
            self.registry.put(actor_id="owner-a", owner_id="owner-a", spec=self.spec(2))
        self.registry.put(actor_id="owner-a", owner_id="owner-a", spec=self.spec())
        with self.assertRaises(ValueError):
            self.registry.put(actor_id="owner-a", owner_id="owner-a", spec=self.spec())

    def test_owner_isolation_and_denied_access(self):
        self.registry.put(actor_id="owner-a", owner_id="owner-a", spec=self.spec())
        self.assertIsNone(self.registry.get(actor_id="owner-b", owner_id="owner-b", spec_id="review"))
        with self.assertRaises(PermissionError):
            self.registry.get(actor_id="intruder", owner_id="owner-a", spec_id="review")

    def test_authorization_exception_fails_closed(self):
        registry = ExecutionSpecRegistry(
            self.path, authorize=lambda *_: (_ for _ in ()).throw(RuntimeError("denied"))
        )
        with self.assertRaises(RuntimeError):
            registry.list_ids(actor_id="owner-a", owner_id="owner-a")

    def test_list_is_bounded_and_deterministic(self):
        self.registry.put(actor_id="owner-a", owner_id="owner-a", spec=self.spec())
        self.assertEqual(self.registry.list_ids(actor_id="owner-a", owner_id="owner-a"), ("review",))
        with self.assertRaises(ValueError):
            self.registry.list_ids(actor_id="owner-a", owner_id="owner-a", limit=0)

    def test_file_backed_database_is_required(self):
        with self.assertRaises(ValueError):
            ExecutionSpecRegistry(":memory:", authorize=lambda *_: True)


if __name__ == "__main__":
    unittest.main()
