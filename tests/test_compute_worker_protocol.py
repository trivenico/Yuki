import unittest

from core.compute_worker_protocol import (
    ComputeWorkerCapabilities,
    ComputeWorkerProtocolError,
    GPUCapability,
)


class ComputeWorkerProtocolTests(unittest.TestCase):
    def worker(self, **overrides):
        values = {
            "worker_id": "gpu-worker-01",
            "protocol_version": 1,
            "paired": True,
            "enabled": True,
            "cpu_cores": 16,
            "ram_total_bytes": 64 * 1024**3,
            "ram_available_bytes": 48 * 1024**3,
            "storage_available_bytes": 500 * 1024**3,
            "backends": ("ollama", "pytorch"),
            "gpus": (GPUCapability("nvidia", "GPU", 24 * 1024**3, 20 * 1024**3, "pytorch"),),
            "last_seen_epoch": 1000,
        }
        values.update(overrides)
        return ComputeWorkerCapabilities(**values)

    def test_maps_fresh_paired_worker_to_scheduler(self):
        record = self.worker().to_scheduler_record(now_epoch=1050)
        self.assertEqual(record["device_id"], "gpu-worker-01")
        self.assertTrue(record["online"])
        self.assertEqual(record["status"], "idle")
        self.assertEqual(record["capabilities"]["gpus"][0]["memory_available_bytes"], 20 * 1024**3)

    def test_stale_worker_is_not_online(self):
        record = self.worker().to_scheduler_record(now_epoch=1200)
        self.assertFalse(record["online"])
        self.assertEqual(record["status"], "degraded")

    def test_unpaired_or_disabled_worker_is_not_online(self):
        self.assertFalse(self.worker(paired=False).to_scheduler_record(now_epoch=1001)["online"])
        self.assertFalse(self.worker(enabled=False).to_scheduler_record(now_epoch=1001)["online"])

    def test_future_heartbeat_fails_closed(self):
        self.assertFalse(self.worker(last_seen_epoch=2000).to_scheduler_record(now_epoch=1000)["online"])

    def test_rejects_unknown_backend(self):
        with self.assertRaises(ComputeWorkerProtocolError):
            self.worker(backends=("unknown",))

    def test_rejects_boolean_resource_values(self):
        with self.assertRaises(ComputeWorkerProtocolError):
            self.worker(cpu_cores=True)

    def test_rejects_impossible_memory(self):
        with self.assertRaises(ComputeWorkerProtocolError):
            self.worker(ram_available_bytes=65 * 1024**3)

    def test_rejects_gpu_memory_overcommit(self):
        with self.assertRaises(ComputeWorkerProtocolError):
            GPUCapability("nvidia", "GPU", 8, 9, "pytorch")

    def test_rejects_unpaired_gpu_inventory_types(self):
        with self.assertRaises(ComputeWorkerProtocolError):
            self.worker(gpus=({"vendor": "nvidia"},))


if __name__ == "__main__":
    unittest.main()
