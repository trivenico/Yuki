import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from evaluation.benchmark_registry import BenchmarkDefinition, BenchmarkRegistry
from evaluation.model_eval import EvalCase


class PersistentBenchmarkRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "benchmarks.sqlite3"
        self.definition = BenchmarkDefinition(
            "reasoning-core", 1, "Reasoning core",
            (EvalCase(
                "retry-case", "reasoning", "Explain bounded retries",
                required_terms=("retry",), max_latency_seconds=5.0,
            ),),
            "Core bounded reasoning checks",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_revision_survives_registry_restart_with_same_digest(self):
        first = BenchmarkRegistry(database_path=self.path)
        stored = first.register(self.definition)
        reopened = BenchmarkRegistry(database_path=self.path)
        loaded = reopened.resolve(
            "reasoning-core", 1, expected_digest=stored.digest,
        )
        self.assertEqual(loaded, self.definition)
        self.assertEqual(loaded.digest, stored.digest)
        self.assertEqual(reopened.list_revisions("reasoning-core"), ((1, stored.digest),))

    def test_same_revision_registration_is_idempotent_after_restart(self):
        first = BenchmarkRegistry(database_path=self.path)
        first.register(self.definition)
        reopened = BenchmarkRegistry(database_path=self.path)
        self.assertEqual(reopened.register(self.definition), self.definition)

    def test_registry_instances_refresh_revisions_written_by_other_process(self):
        first = BenchmarkRegistry(database_path=self.path)
        second = BenchmarkRegistry(database_path=self.path)
        first.register(self.definition)
        revision_two = BenchmarkDefinition(
            "reasoning-core", 2, "Reasoning core v2",
            self.definition.cases,
        )
        first.register(revision_two)
        loaded = second.resolve(
            "reasoning-core", 2, expected_digest=revision_two.digest,
        )
        self.assertEqual(loaded, revision_two)
        self.assertEqual(len(second.list_revisions("reasoning-core")), 2)

    def test_immutable_revision_rejects_changed_content(self):
        registry = BenchmarkRegistry(database_path=self.path)
        registry.register(self.definition)
        changed = BenchmarkDefinition(
            "reasoning-core", 1, "Changed title",
            self.definition.cases, self.definition.description,
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            registry.register(changed)

    def test_new_revision_must_increment_version(self):
        registry = BenchmarkRegistry(database_path=self.path)
        registry.register(self.definition)
        skipped = BenchmarkDefinition(
            "reasoning-core", 3, "Skipped revision",
            self.definition.cases,
        )
        with self.assertRaisesRegex(ValueError, "increment"):
            registry.register(skipped)

    def test_detects_persisted_payload_tampering_on_restart(self):
        registry = BenchmarkRegistry(database_path=self.path)
        registry.register(self.definition)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE benchmark_revisions SET payload=? WHERE benchmark_id=? AND version=?",
                ('{"benchmark_id":"forged"}', "reasoning-core", 1),
            )
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            BenchmarkRegistry(database_path=self.path)


if __name__ == "__main__":
    unittest.main()
