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


    def test_fractional_cpu_requirements_use_millicores(self):
        result = self.scheduler.plan(
            ModelRequirements("org/model", 1000, cpu_millicores=1500),
            [{
                "device_id": "fractional", "online": True, "status": "idle",
                "capabilities": {"cpu_millicores": 1750, "memory_bytes": 4000},
            }],
        )
        self.assertEqual(result.device_id, "fractional")
        rejected = self.scheduler.plan(
            ModelRequirements("org/model", 1000, cpu_millicores=1800),
            [{
                "device_id": "fractional", "online": True, "status": "idle",
                "capabilities": {"cpu_millicores": 1750, "memory_bytes": 4000},
            }],
        )
        self.assertIsNone(rejected.device_id)

    def test_reserve_placement_requires_authoritative_reservation(self):
        class FakeReservations:
            def __init__(self):
                self.calls = []

            def reserve(self, session, device_id, task_id, **kwargs):
                self.calls.append((session, device_id, task_id, kwargs))
                if device_id == "best-fit":
                    from core.device_resource_reservations import CapacityUnavailable
                    raise CapacityUnavailable("capacity_changed")
                return {"device_id": device_id, "fencing_token": 7}

        fake = FakeReservations()
        devices = [
            device("best-fit", ram=5000),
            device("fallback", ram=6000),
        ]
        decision, reservation = self.scheduler.reserve_placement(
            ModelRequirements("org/model", 4000, cpu_millicores=1500),
            devices,
            reservation_service=fake,
            session_token="session",
            task_id="task-1",
            idempotency_key="idem-1",
            reservation_secret="s" * 40,
        )
        self.assertEqual(decision.status, "reserved")
        self.assertEqual(decision.device_id, "fallback")
        self.assertEqual(reservation["fencing_token"], 7)
        self.assertEqual([call[1] for call in fake.calls], ["best-fit", "fallback"])
        self.assertEqual(fake.calls[1][3]["cpu_millicores"], 1500)

    def test_reserve_placement_does_not_admit_without_reservation(self):
        from core.device_resource_reservations import CapacityUnavailable

        class UnavailableReservations:
            def reserve(self, *args, **kwargs):
                raise CapacityUnavailable("capacity_changed")

        decision, reservation = self.scheduler.reserve_placement(
            ModelRequirements("org/model", 1000),
            [device("node-a")],
            reservation_service=UnavailableReservations(),
            session_token="session",
            task_id="task-2",
            idempotency_key="idem-2",
            reservation_secret="s" * 40,
        )
        self.assertEqual(decision.status, "unscheduled")
        self.assertIsNone(decision.device_id)
        self.assertIsNone(reservation)
        self.assertEqual(
            decision.rejected[0]["reason"], "reservation_capacity_unavailable"
        )


if __name__ == "__main__":
    unittest.main()
