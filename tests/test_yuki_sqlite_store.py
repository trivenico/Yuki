"""Tests for transactional SQLite JSON state storage."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from yuki_sqlite_store import SQLiteJsonStore


class SQLiteJsonStoreTests(unittest.TestCase):
    def test_round_trip_and_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteJsonStore(Path(folder) / "state.sqlite3")
            self.assertIsNone(store.revision("memory"))
            store.write_json("memory", ["hello", "こんにちは"])
            self.assertEqual(store.read_json("memory"), ["hello", "こんにちは"])
            self.assertEqual(store.revision("memory"), 1)
            store.write_json("memory", ["updated"])
            self.assertEqual(store.revision("memory"), 2)

    def test_update_is_atomic_and_rolls_back_on_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteJsonStore(Path(folder) / "state.sqlite3")
            store.write_json("counter", {"value": 1})

            def fail(value):
                value["value"] = 99
                raise RuntimeError("abort update")

            with self.assertRaisesRegex(RuntimeError, "abort update"):
                store.update_json("counter", fail)
            self.assertEqual(store.read_json("counter"), {"value": 1})
            self.assertEqual(store.revision("counter"), 1)

    def test_concurrent_updates_do_not_lose_increments(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteJsonStore(Path(folder) / "state.sqlite3", timeout_seconds=10)
            store.write_json("counter", {"value": 0})

            def increment(_):
                return store.update_json(
                    "counter",
                    lambda state: {"value": state["value"] + 1},
                    default={"value": 0},
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(increment, range(80)))

            self.assertEqual(store.read_json("counter"), {"value": 80})
            self.assertEqual(store.revision("counter"), 81)

    def test_rejects_empty_keys_and_non_finite_json(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteJsonStore(Path(folder) / "state.sqlite3")
            with self.assertRaises(ValueError):
                store.write_json("", {"ok": True})
            with self.assertRaises(ValueError):
                store.write_json("bad", {"number": float("nan")})
            self.assertIsNone(store.read_json("bad"))

    def test_invalid_timeout_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                SQLiteJsonStore(Path(folder) / "state.sqlite3", timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
