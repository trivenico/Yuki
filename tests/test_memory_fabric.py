import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.memory_fabric import (
    MemoryFabric,
    MemoryFabricClosed,
    MemoryFabricCorruption,
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
