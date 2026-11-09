import tempfile
import unittest
from pathlib import Path

from core.device_agent import DeviceAgentService, heartbeat_to_dict
from core.device_pairing import DevicePairingService


class DeviceAgentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.pairing = DevicePairingService(root / "pairing.sqlite")
        self.agent = DeviceAgentService(root / "agent.sqlite", self.pairing)
        challenge = self.pairing.create_pairing_challenge("owner-a")
        self.credential = self.pairing.pair_device(challenge.code, "Yuki Desktop")

    def heartbeat(self, sequence=1, **kwargs):
        values = {"status": "idle", "capabilities": {
            "os": "Linux", "architecture": "x86_64", "cpu_cores": 12,
            "ram_total_bytes": 32_000_000, "ram_available_bytes": 20_000_000,
            "gpus": [{"name": "GPU", "memory_total_bytes": 8_000_000,
                      "memory_available_bytes": 6_000_000}],
            "storage_free_bytes": 100_000_000,
        }}
        values.update(kwargs)
        return self.agent.heartbeat(self.credential.token, sequence=sequence, **values)

    def test_authenticated_heartbeat_and_owner_scoped_listing(self):
        result = self.heartbeat()
        self.assertEqual(result.device_id, self.credential.device.device_id)
        self.assertTrue(result.online)
        self.assertEqual(self.agent.list_devices("owner-a"), [result])
        self.assertEqual(self.agent.list_devices("owner-b"), [])

    def test_sequence_prevents_replay_and_out_of_order_updates(self):
        self.heartbeat(sequence=5)
        for sequence in (5, 4):
            with self.subTest(sequence=sequence), self.assertRaisesRegex(ValueError, "stale_heartbeat"):
                self.heartbeat(sequence=sequence)
        self.assertEqual(self.agent.list_devices("owner-a")[0].sequence, 5)

    def test_revoked_device_cannot_send_heartbeat(self):
        self.heartbeat()
        self.assertTrue(self.agent.revoke_device("owner-a", self.credential.device.device_id))
        with self.assertRaises(PermissionError):
            self.heartbeat(sequence=2)

    def test_invalid_device_token_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.agent.heartbeat("invalid-token", sequence=1, status="idle", capabilities={})

    def test_capability_schema_rejects_unexpected_fields(self):
        with self.assertRaisesRegex(ValueError, "invalid_capabilities"):
            self.heartbeat(capabilities={"os": "Linux", "shell_command": "whoami"})

    def test_capability_schema_rejects_impossible_memory(self):
        with self.assertRaisesRegex(ValueError, "invalid_capabilities"):
            self.heartbeat(capabilities={"ram_total_bytes": 100, "ram_available_bytes": 200})

    def test_resource_capability_totals_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "invalid_capabilities"):
            self.heartbeat(capabilities={"cpu_cores": 4097})
        with self.assertRaisesRegex(ValueError, "invalid_capabilities"):
            self.heartbeat(capabilities={
                "gpus": [
                    {"memory_available_bytes": 2**60},
                    {"memory_available_bytes": 2**60},
                ],
            })

    def test_status_and_sequence_are_validated(self):
        with self.assertRaisesRegex(ValueError, "invalid_status"):
            self.heartbeat(status="execute")
        with self.assertRaisesRegex(ValueError, "invalid_sequence"):
            self.heartbeat(sequence=True)

    def test_gpu_metadata_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "invalid_capabilities"):
            self.heartbeat(capabilities={"gpus": [{} for _ in range(9)]})

    def test_heartbeat_publishes_authenticated_resource_capacity(self):
        class Reservations:
            def __init__(self):
                self.calls = []
            def publish_capacity(self, token, **capacity):
                self.calls.append((token, capacity))
            def advance_capacity_sequence(self, token, *, source_sequence):
                pass
        reservations = Reservations()
        root = Path(self.temp.name)
        agent = DeviceAgentService(
            root / "agent-with-capacity.sqlite", self.pairing,
            reservation_service=reservations,
        )
        result = agent.heartbeat(
            self.credential.token, sequence=1, status="idle",
            capabilities={
                "cpu_millicores": 1750,
                "ram_total_bytes": 10_000,
                "ram_available_bytes": 7000,
                "gpus": [{"memory_total_bytes": 4000, "memory_available_bytes": 2500}],
            },
        )
        self.assertEqual(result.device_id, self.credential.device.device_id)
        self.assertEqual(len(reservations.calls), 1)
        self.assertEqual(reservations.calls[0][0], self.credential.token)
        self.assertEqual(reservations.calls[0][1], {
            "memory_bytes": 7000, "cpu_millicores": 1750,
            "gpu_memory_bytes": 2500, "source_sequence": 1,
        })

    def test_explicit_empty_gpu_inventory_publishes_zero_gpu_capacity(self):
        class Reservations:
            def __init__(self):
                self.calls = []
            def publish_capacity(self, token, **capacity):
                self.calls.append(capacity)
            def advance_capacity_sequence(self, token, *, source_sequence):
                pass
        reservations = Reservations()
        agent = DeviceAgentService(
            Path(self.temp.name) / "agent-zero-gpu.sqlite", self.pairing,
            reservation_service=reservations,
        )
        agent.heartbeat(
            self.credential.token, sequence=3, status="idle",
            capabilities={
                "cpu_cores": 2, "ram_total_bytes": 8000,
                "ram_available_bytes": 5000, "gpus": [],
            },
        )
        self.assertEqual(reservations.calls, [{
            "memory_bytes": 5000, "cpu_millicores": 2000,
            "gpu_memory_bytes": 0, "source_sequence": 3,
        }])

    def test_incomplete_capacity_does_not_publish_or_create_outbox(self):
        class Reservations:
            def __init__(self):
                self.calls = []
                self.watermarks = []
            def publish_capacity(self, token, **capacity):
                self.calls.append(capacity)
            def advance_capacity_sequence(self, token, *, source_sequence):
                self.watermarks.append(source_sequence)
        reservations = Reservations()
        root = Path(self.temp.name)
        agent = DeviceAgentService(
            root / "agent-incomplete.sqlite", self.pairing,
            reservation_service=reservations,
        )
        agent.heartbeat(
            self.credential.token, sequence=1, status="idle",
            capabilities={"cpu_cores": 4, "ram_available_bytes": 5000},
        )
        self.assertEqual(reservations.calls, [])
        self.assertEqual(reservations.watermarks, [1])
        self.assertFalse(agent.retry_pending_capacity(self.credential.token))

    def test_failed_capacity_publish_is_durable_and_retryable(self):
        class Reservations:
            def __init__(self):
                self.fail = True
                self.calls = []
            def publish_capacity(self, token, **capacity):
                if self.fail:
                    raise RuntimeError("temporary ledger outage")
                self.calls.append(capacity)
            def advance_capacity_sequence(self, token, *, source_sequence):
                pass
        reservations = Reservations()
        root = Path(self.temp.name)
        agent = DeviceAgentService(
            root / "agent-outbox.sqlite", self.pairing,
            reservation_service=reservations,
        )
        with self.assertRaisesRegex(RuntimeError, "temporary ledger outage"):
            agent.heartbeat(
                self.credential.token, sequence=1, status="idle",
                capabilities={
                    "cpu_cores": 4, "ram_total_bytes": 9000,
                    "ram_available_bytes": 6000, "gpus": [],
                },
            )
        self.assertEqual(agent.list_devices("owner-a")[0].sequence, 1)
        reservations.fail = False
        self.assertTrue(agent.retry_pending_capacity(self.credential.token))
        self.assertEqual(reservations.calls[0], {
            "memory_bytes": 6000, "cpu_millicores": 4000,
            "gpu_memory_bytes": 0, "source_sequence": 1,
        })
        self.assertFalse(agent.retry_pending_capacity(self.credential.token))

    def test_serialized_report_has_only_declared_fields(self):
        result = heartbeat_to_dict(self.heartbeat())
        self.assertEqual(set(result), {
            "device_id", "status", "sequence", "reported_at", "last_seen_at",
            "capabilities", "online",
        })


if __name__ == "__main__":
    unittest.main()
