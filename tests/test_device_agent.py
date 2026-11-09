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

    def test_status_and_sequence_are_validated(self):
        with self.assertRaisesRegex(ValueError, "invalid_status"):
            self.heartbeat(status="execute")
        with self.assertRaisesRegex(ValueError, "invalid_sequence"):
            self.heartbeat(sequence=True)

    def test_gpu_metadata_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "invalid_capabilities"):
            self.heartbeat(capabilities={"gpus": [{} for _ in range(9)]})

    def test_serialized_report_has_only_declared_fields(self):
        result = heartbeat_to_dict(self.heartbeat())
        self.assertEqual(set(result), {
            "device_id", "status", "sequence", "reported_at", "last_seen_at",
            "capabilities", "online",
        })


if __name__ == "__main__":
    unittest.main()
