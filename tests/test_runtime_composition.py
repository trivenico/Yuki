import tempfile
import unittest
from pathlib import Path

from core.runtime_composition import build_runtime


class RuntimeCompositionTests(unittest.TestCase):
    def test_only_registered_health_task_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = build_runtime({"workspace": tmp}, max_workers=1)
            with self.assertRaises(ValueError):
                service.submit("arbitrary.command", {})
            job = service.submit("system.health", {"component": "runtime"})
            result = service.run_one(worker_id="composition-test")
            self.assertEqual(result.id, job.id)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.result, {"component": "runtime", "healthy": True, "version": 1})
            service.close()

    def test_authorizer_rejects_payload_task_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = build_runtime({"workspace": tmp}, max_workers=1)
            with self.assertRaises(PermissionError):
                service.submit("system.health", {"component": "filesystem"})
            service.close()


if __name__ == "__main__":
    unittest.main()
