import unittest

from core.distributed_model_scheduler import (
    DistributedModelScheduler, ModelRequirements,
)


def device(device_id, *, online=True, status="idle", cpu=8, ram=16_000,
           gpu=0):
    caps = {"cpu_cores": cpu, "ram_available_bytes": ram}
    if gpu:
        caps["gpus"] = [{"memory_available_bytes": gpu}]
    return {
        "device_id": device_id, "online": online, "status": status,
        "capabilities": caps,
    }


class DistributedModelSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = DistributedModelScheduler()

    def test_best_fit_selects_smallest_sufficient_device(self):
        requirements = ModelRequirements("org/model", memory_bytes=4000)
        result = self.scheduler.plan(requirements, [
            device("large", ram=32000), device("small", ram=5000),
        ])
        self.assertEqual(result.device_id, "small")
        self.assertEqual(result.status, "planned")
        self.assertEqual(result.eligible_device_ids, ("small", "large"))

    def test_offline_busy_and_insufficient_nodes_are_rejected(self):
        result = self.scheduler.plan(ModelRequirements("org/model", 1000), [
            device("offline", online=False),
            device("busy", status="busy"),
            device("low-ram", ram=500),
        ])
        self.assertIsNone(result.device_id)
        self.assertEqual(result.status, "unscheduled")
        self.assertEqual(
            {x["reason"] for x in result.rejected},
            {"offline", "device_not_idle", "insufficient_or_unknown_ram"},
        )

    def test_gpu_requirements_are_explicit(self):
        requirements = ModelRequirements(
            "org/model", memory_bytes=1000, gpu_memory_bytes=4000, require_gpu=True
        )
        result = self.scheduler.plan(requirements, [
            device("cpu-only", ram=5000),
            device("gpu-low", ram=5000, gpu=2000),
            device("gpu-fit", ram=5000, gpu=8000),
        ])
        self.assertEqual(result.device_id, "gpu-fit")
        self.assertEqual(result.rejected[0]["reason"], "gpu_required")

    def test_allowlist_is_enforced(self):
        result = self.scheduler.plan(
            ModelRequirements("org/model", 1000),
            [device("a"), device("b")], allowed_device_ids=["b"],
        )
        self.assertEqual(result.device_id, "b")
        self.assertEqual(result.rejected[0]["reason"], "not_in_allowlist")

    def test_unknown_resources_fail_closed(self):
        result = self.scheduler.plan(
            ModelRequirements("org/model", 1000),
            [{"device_id": "unknown", "online": True, "status": "idle", "capabilities": {}}],
        )
        self.assertIsNone(result.device_id)
        self.assertEqual(result.rejected[0]["reason"], "insufficient_or_unknown_cpu")

    def test_duplicate_and_malformed_devices_rejected(self):
        requirements = ModelRequirements("org/model", 1000)
        with self.assertRaisesRegex(ValueError, "duplicate_device_id"):
            self.scheduler.plan(requirements, [device("a"), device("a")])
        with self.assertRaisesRegex(ValueError, "invalid_device_id"):
            self.scheduler.plan(requirements, [device("../bad")])

    def test_device_and_input_limits(self):
        with self.assertRaisesRegex(ValueError, "device_limit_exceeded"):
            DistributedModelScheduler(max_devices=1).plan(
                ModelRequirements("org/model", 1000), [device("a"), device("b")]
            )
        with self.assertRaises(ValueError):
            ModelRequirements("org/model", 1000, require_gpu=True)

    def test_decision_is_plan_only_and_serializable(self):
        result = self.scheduler.plan(
            ModelRequirements("org/model", 1000), [device("a")]
        ).to_dict()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["reason"], "best_fit_single_device; dispatch_not_performed")
        self.assertNotIn("command", result)
        self.assertNotIn("execution_id", result)


if __name__ == "__main__":
    unittest.main()
