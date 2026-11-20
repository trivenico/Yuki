import concurrent.futures
import fnmatch
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from core.json_file_store import atomic_write_json, file_lock


class JsonFileStoreTests(unittest.TestCase):
    def test_atomic_write_creates_private_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            atomic_write_json(path, {"value": 3})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": 3})
            self.assertFalse(list(path.parent.glob("*.tmp")))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_independent_memory_instances_do_not_lose_concurrent_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"

            def append_batch(batch):
                store = MemoryStore(path)
                for index in range(20):
                    store.add(f"batch-{batch}-item-{index}")

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(append_batch, range(6)))

            entries = MemoryStore(path).list(limit=200)
            self.assertEqual(len(entries), 120)
            self.assertEqual(len({item["id"] for item in entries}), 120)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink semantics")
    def test_lock_file_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("untouched", encoding="utf-8")
            lock = Path(directory) / "state.json.lock"
            lock.symlink_to(target)
            with self.assertRaises((OSError, RuntimeError)):
                with file_lock(Path(directory) / "state.json"):
                    self.fail("symlink lock must not be followed")
            self.assertEqual(target.read_text(encoding="utf-8"), "untouched")

    def test_gitignore_excludes_local_transaction_and_sqlite_artifacts(self):
        root = Path(__file__).resolve().parents[1]
        patterns = [
            line.strip() for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        artifacts = (
            "yuki_memory.json.lock",
            "yuki_auth.sqlite3",
            "auth.sqlite3-wal",
            "auth.sqlite3-shm",
        )
        for artifact in artifacts:
            self.assertTrue(
                any(fnmatch.fnmatch(artifact, pattern) for pattern in patterns),
                f"{artifact} is not covered by .gitignore",
            )


if __name__ == "__main__":
    unittest.main()
