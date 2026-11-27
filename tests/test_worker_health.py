import json
import os
import tempfile
import unittest
from pathlib import Path

from core.worker_health import read_worker_health, write_worker_health


class WorkerHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.root.mkdir(mode=0o700)
        if os.name == "posix":
            self.root.chmod(0o700)
        self.path = self.root / "worker-health.json"

    def test_atomic_snapshot_is_private_and_freshness_is_explicit(self):
        write_worker_health(self.path, observed_at=100, details={"phase": "idle", "pid": 42})
        self.assertEqual(read_worker_health(self.path, now=110, max_age_seconds=20).state, "fresh")
        stale = read_worker_health(self.path, now=121, max_age_seconds=20)
        self.assertEqual(stale.state, "stale")
        self.assertEqual(stale.details, {"phase": "idle", "pid": 42})
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_pid_is_only_data_and_does_not_prove_liveness(self):
        write_worker_health(self.path, observed_at=100, details={"pid": 9})
        result = read_worker_health(self.path, now=100)
        self.assertEqual(result.state, "fresh")
        self.assertEqual(result.details["pid"], 9)
        self.assertNotIn("alive", result.details)

    def test_missing_and_malformed_records_are_not_ready(self):
        self.assertEqual(read_worker_health(self.path).state, "missing")
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(read_worker_health(self.path).state, "invalid")

    def test_rejects_permissive_parent_and_oversized_payload(self):
        if os.name == "posix":
            self.root.chmod(0o755)
            with self.assertRaises(PermissionError):
                write_worker_health(self.path, details={})
            self.root.chmod(0o700)
        with self.assertRaises(ValueError):
            write_worker_health(self.path, details={"payload": "x" * 20000})

    def test_reader_rejects_nonfinite_age_and_boolean_schema(self):
        with self.assertRaises(ValueError):
            read_worker_health(self.path, max_age_seconds=float("nan"))
        with self.assertRaises(ValueError):
            read_worker_health(self.path, max_age_seconds=float("inf"))
        self.path.write_text(json.dumps({"schema_version": True, "observed_at": 1, "details": {}}))
        self.assertEqual(read_worker_health(self.path, now=2).state, "invalid")

    def test_reader_rejects_unknown_schema(self):
        self.path.write_text(json.dumps({"schema_version": 999, "observed_at": 1, "details": {}}))
        self.assertEqual(read_worker_health(self.path, now=2).state, "invalid")


if __name__ == "__main__":
    unittest.main()
