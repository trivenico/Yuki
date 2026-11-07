import json
import tempfile
import unittest
from pathlib import Path

from memory.collective_vault import CollectiveMemoryVault, MemoryVaultError


class CollectiveMemoryVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.allowed = True
        self.vault = CollectiveMemoryVault(
            str(Path(self.tmp.name) / "memory.sqlite3"),
            authorize=lambda actor, action, resource: (
                self.allowed and actor in {"alice", "agent-a", "agent-b"}
                and action in {
                    "memory:write", "memory:update", "memory:read",
                    "memory:history", "memory:delete", "memory:export",
                }
            ),
        )

    def remember(self, **kwargs):
        defaults = dict(
            actor_id="alice", scope="project", scope_id="yuki",
            category="decision", content="Use SQLite for durable project memory.",
            source_type="decision_record", source_ref="architecture/adr-001",
        )
        defaults.update(kwargs)
        return self.vault.remember(**defaults)

    def test_create_update_and_history(self):
        first = self.remember()
        second = self.remember(memory_id=first.memory_id, content="Use SQLite with audited versions.")
        self.assertEqual(second.version, 2)
        history = self.vault.history(actor_id="alice", memory_id=first.memory_id)
        self.assertEqual([item["version"] for item in history], [1, 2])
        self.assertIn("audited versions", history[1]["content"])

    def test_search_is_scoped_and_permission_filtered(self):
        self.remember()
        self.remember(scope="private", scope_id="alice-private", content="SQLite private note")
        results = self.vault.search(
            actor_id="alice", query="SQLite durable memory",
            scopes=(("project", "yuki"),),
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].record.scope_id, "yuki")

    def test_denied_scope_is_not_searched(self):
        self.remember()
        self.allowed = False
        results = self.vault.search(
            actor_id="alice", query="SQLite", scopes=(("project", "yuki"),)
        )
        self.assertEqual(results, ())

    def test_actor_cannot_spoof_creator(self):
        with self.assertRaises(PermissionError):
            self.remember(actor_id="mallory")

    def test_memory_scope_cannot_be_changed(self):
        first = self.remember()
        with self.assertRaises(MemoryVaultError):
            self.remember(memory_id=first.memory_id, scope="team", scope_id="engineering")

    def test_delete_is_soft_and_cannot_be_overwritten(self):
        first = self.remember()
        self.assertTrue(self.vault.forget(actor_id="alice", memory_id=first.memory_id))
        self.assertIsNone(self.vault.get(actor_id="alice", memory_id=first.memory_id))
        with self.assertRaises(MemoryVaultError):
            self.remember(memory_id=first.memory_id, content="Resurrect")

    def test_export_and_import_portable_json(self):
        self.remember()
        payload = self.vault.export_records(
            actor_id="alice", scopes=(("project", "yuki"),)
        )
        parsed = json.loads(payload)
        self.assertEqual(parsed["schema_version"], 1)
        other = CollectiveMemoryVault(
            str(Path(self.tmp.name) / "other.sqlite3"), authorize=lambda *_: True
        )
        self.assertEqual(other.import_records(actor_id="alice", payload=payload), 1)
        results = other.search(
            actor_id="alice", query="SQLite", scopes=(("project", "yuki"),)
        )
        self.assertEqual(len(results), 1)

    def test_export_requires_explicit_export_permission(self):
        vault = CollectiveMemoryVault(
            str(Path(self.tmp.name) / "no-export.sqlite3"),
            authorize=lambda actor, action, resource: action != "memory:export",
        )
        with self.assertRaises(PermissionError):
            vault.export_records(actor_id="alice", scopes=(("project", "yuki"),))

    def test_import_rejects_invalid_and_oversized_payload(self):
        with self.assertRaises(MemoryVaultError):
            self.vault.import_records(actor_id="alice", payload=b"not-json")
        with self.assertRaises(MemoryVaultError):
            self.vault.import_records(actor_id="alice", payload=b"x" * 10_000_001)

    def test_authorization_callback_failure_fails_closed(self):
        vault = CollectiveMemoryVault(
            str(Path(self.tmp.name) / "fail-closed.sqlite3"),
            authorize=lambda *_: (_ for _ in ()).throw(RuntimeError("ACL unavailable")),
        )
        with self.assertRaises(PermissionError):
            vault.remember(
                actor_id="alice", scope="project", scope_id="yuki",
                category="fact", content="No access", source_type="test",
                source_ref="test",
            )


if __name__ == "__main__":
    unittest.main()
