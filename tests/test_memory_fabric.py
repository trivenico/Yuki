import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from core.memory_fabric import (
    MemoryFabric,
    MemoryFabricClosed,
    MemoryFabricCorruption,
    MemoryFabricError,
)


class MemoryFabricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "memory-fabric.sqlite"
        self.fabrics = []
        self.addCleanup(self._close_fabrics)

    def _make_fabric(self, *args, **kwargs):
        fabric = MemoryFabric(*args, **kwargs)
        self.fabrics.append(fabric)
        return fabric

    def _close_fabrics(self):
        for fabric in self.fabrics:
            fabric.close()

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_storage_directory_and_database_are_private(self):
        self._make_fabric(self.path, ram_capacity_bytes=0, disk_capacity_bytes=100)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_rejects_group_or_world_accessible_storage_directory(self):
        os.chmod(self.tmp.name, 0o755)
        with self.assertRaises(MemoryFabricError):
            MemoryFabric(self.path)

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_rejects_group_or_world_accessible_existing_database(self):
        self.path.write_bytes(b"")
        os.chmod(self.path, 0o644)
        with self.assertRaises(MemoryFabricError):
            MemoryFabric(self.path)

    def test_put_and_get_promotes_ssd_entry_into_bounded_ram(self):
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=5, disk_capacity_bytes=100,
        )
        fabric.put("models", "layer-1", b"1234")
        self.assertEqual(fabric.get("models", "layer-1"), b"1234")
        stats = fabric.stats()
        self.assertEqual(stats["ram_resident_bytes"], 4)
        self.assertEqual(stats["disk_used_bytes"], 4)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 0)

    def test_ram_lru_evicts_hot_entries_without_losing_ssd_copy(self):
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=5, disk_capacity_bytes=100,
        )
        fabric.put("cache", "a", b"1234")
        fabric.put("cache", "b", b"5678")
        stats = fabric.stats()
        self.assertEqual(stats["ram_resident_bytes"], 4)
        self.assertEqual(stats["ram_entries"], 1)
        self.assertEqual(stats["evictions"], 1)
        self.assertEqual(fabric.get("cache", "a"), b"1234")
        self.assertEqual(fabric.get("cache", "b"), b"5678")

    def test_ssd_cache_survives_fabric_reconstruction(self):
        first = self._make_fabric(self.path, ram_capacity_bytes=0, disk_capacity_bytes=100)
        first.put("agent-state", "agent-7", b"checkpoint")
        first.close()
        second = self._make_fabric(self.path, ram_capacity_bytes=10, disk_capacity_bytes=100)
        self.assertEqual(second.get("agent-state", "agent-7"), b"checkpoint")

    def test_disk_capacity_evicts_least_recently_used_entries(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=0, disk_capacity_bytes=6)
        fabric.put("cache", "old", b"1111")
        self.assertEqual(fabric.get("cache", "old"), b"1111")
        fabric.put("cache", "new", b"2222")
        self.assertIsNone(fabric.get("cache", "old"))
        self.assertEqual(fabric.get("cache", "new"), b"2222")
        self.assertEqual(fabric.stats()["disk_used_bytes"], 4)

    def test_failed_put_rolls_back_ssd_eviction_without_invalidating_ram(self):
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=8, disk_capacity_bytes=6
        )
        fabric.put("cache", "existing", b"1111")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER reject_memory_fabric_insert "
                "BEFORE INSERT ON memory_fabric_entries "
                "BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END"
            )
        before = fabric.stats()
        with self.assertRaises(sqlite3.IntegrityError):
            fabric.put("cache", "replacement", b"2222")
        self.assertIn(("cache", "existing"), fabric._ram)
        self.assertEqual(fabric.get("cache", "existing"), b"1111")
        self.assertIsNone(fabric.get("cache", "replacement"))
        after = fabric.stats()
        self.assertEqual(after["disk_used_bytes"], before["disk_used_bytes"])
        self.assertEqual(after["evictions"], before["evictions"])

    def test_failed_delete_preserves_ram_and_ssd_entry(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=8, disk_capacity_bytes=32)
        fabric.put("cache", "entry", b"value")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER reject_memory_fabric_delete "
                "BEFORE DELETE ON memory_fabric_entries "
                "BEGIN SELECT RAISE(ABORT, 'injected delete failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            fabric.delete("cache", "entry")
        self.assertIn(("cache", "entry"), fabric._ram)
        self.assertEqual(fabric.get("cache", "entry"), b"value")
        self.assertEqual(fabric.stats()["disk_entries"], 1)

    def test_failed_namespace_clear_preserves_ram_and_ssd_entries(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=8, disk_capacity_bytes=32)
        fabric.put("cache", "entry", b"value")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER reject_memory_fabric_clear "
                "BEFORE DELETE ON memory_fabric_entries "
                "BEGIN SELECT RAISE(ABORT, 'injected clear failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            fabric.clear("cache")
        self.assertIn(("cache", "entry"), fabric._ram)
        self.assertEqual(fabric.get("cache", "entry"), b"value")
        self.assertEqual(fabric.stats()["disk_entries"], 1)

    def test_failed_global_clear_preserves_ram_and_ssd_entries(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=8, disk_capacity_bytes=32)
        fabric.put("cache", "entry", b"value")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER reject_memory_fabric_global_clear "
                "BEFORE DELETE ON memory_fabric_entries "
                "BEGIN SELECT RAISE(ABORT, 'injected clear failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            fabric.clear()
        self.assertIn(("cache", "entry"), fabric._ram)
        self.assertEqual(fabric.get("cache", "entry"), b"value")
        self.assertEqual(fabric.stats()["disk_entries"], 1)

    def test_ram_eviction_discards_pending_recency_metadata(self):
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=4, disk_capacity_bytes=32
        )
        fabric.put("cache", "first", b"1111")
        self.assertEqual(fabric.get("cache", "first"), b"1111")
        self.assertIn(("cache", "first"), fabric._pending_ram_accesses)
        fabric.put("cache", "second", b"2222")
        self.assertNotIn(("cache", "first"), fabric._pending_ram_accesses)
        self.assertLessEqual(
            len(fabric._pending_ram_accesses), fabric.stats()["ram_entries"]
        )

    def test_namespace_clear_discards_pending_recency_metadata(self):
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=8, disk_capacity_bytes=32
        )
        fabric.put("one", "first", b"1111")
        fabric.put("two", "second", b"2222")
        fabric.get("one", "first")
        fabric.get("two", "second")
        fabric.clear("one")
        self.assertNotIn(("one", "first"), fabric._pending_ram_accesses)
        self.assertIn(("two", "second"), fabric._pending_ram_accesses)

    def test_ram_hits_refresh_ssd_eviction_recency_on_next_write(self):
        ticks = iter(float(value) for value in range(1, 20))
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=8, disk_capacity_bytes=8,
            clock=lambda: next(ticks),
        )
        fabric.put("cache", "old", b"1111")
        fabric.put("cache", "new", b"2222")
        self.assertEqual(fabric.get("cache", "old"), b"1111")
        fabric.put("cache", "third", b"3333")
        self.assertEqual(fabric.get("cache", "old"), b"1111")
        self.assertIsNone(fabric.get("cache", "new"))
        self.assertEqual(fabric.get("cache", "third"), b"3333")

    def test_ssd_eviction_invalidates_the_corresponding_ram_entry(self):
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=8, disk_capacity_bytes=6,
        )
        fabric.put("cache", "old", b"1111")
        self.assertEqual(fabric.get("cache", "old"), b"1111")
        self.assertEqual(fabric.stats()["ram_entries"], 1)
        fabric.put("cache", "new", b"2222")
        self.assertIsNone(fabric.get("cache", "old"))
        self.assertEqual(fabric.get("cache", "new"), b"2222")
        stats = fabric.stats()
        self.assertEqual(stats["ram_entries"], 1)
        self.assertEqual(stats["ram_resident_bytes"], 4)

    def test_expired_entries_are_cache_misses(self):
        now = [1000.0]
        fabric = self._make_fabric(
            self.path, ram_capacity_bytes=100, disk_capacity_bytes=100,
            clock=lambda: now[0],
        )
        fabric.put("cache", "temporary", b"value", ttl_seconds=5)
        now[0] += 6
        self.assertIsNone(fabric.get("cache", "temporary"))
        self.assertEqual(fabric.stats()["disk_entries"], 0)

    def test_corrupt_ssd_payload_is_deleted_and_reported(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=0, disk_capacity_bytes=100)
        fabric.put("cache", "payload", b"original")
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE memory_fabric_entries SET payload=? WHERE namespace=? AND cache_key=?",
                (b"tampered", "cache", "payload"),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(MemoryFabricCorruption):
            fabric.get("cache", "payload")
        self.assertIsNone(fabric.get("cache", "payload"))
        self.assertEqual(fabric.stats()["corruptions"], 1)

    def test_only_bounded_bytes_and_valid_ttls_are_accepted(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=10, disk_capacity_bytes=100)
        for value in ("text", {"unsafe": "object"}, 123):
            with self.subTest(value=value), self.assertRaises(TypeError):
                fabric.put("cache", "bad", value)
        for ttl in (True, 0, -1, float("inf"), MemoryFabric.MAX_TTL_SECONDS + 1):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                fabric.put("cache", "bad-ttl", b"value", ttl_seconds=ttl)
        with self.assertRaises(ValueError):
            MemoryFabric(":memory:")

    def test_namespace_clear_is_scoped(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=100, disk_capacity_bytes=100)
        fabric.put("agent-a", "state", b"a")
        fabric.put("agent-b", "state", b"b")
        self.assertEqual(fabric.clear("agent-a"), 1)
        self.assertIsNone(fabric.get("agent-a", "state"))
        self.assertEqual(fabric.get("agent-b", "state"), b"b")

    def test_close_clears_ram_and_rejects_future_operations(self):
        fabric = self._make_fabric(self.path, ram_capacity_bytes=100, disk_capacity_bytes=100)
        fabric.put("cache", "entry", b"value")
        fabric.close()
        with self.assertRaises(MemoryFabricClosed):
            fabric.get("cache", "entry")
        self.assertEqual(fabric._ram_bytes, 0)


if __name__ == "__main__":
    unittest.main()
