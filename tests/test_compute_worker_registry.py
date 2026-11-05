import tempfile
import unittest
from pathlib import Path

from core.compute_worker_protocol import ComputeWorkerCapabilities, GPUCapability
from core.compute_worker_registry import (
    ComputeWorkerRegistrationError,
    PairedComputeWorkerRegistry,
)
from core.device_pairing import DevicePairingService


class PairedComputeWorkerRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pairing = DevicePairingService(Path(self.temp.name) / "pairing.sqlite")
        self.registry = PairedComputeWorkerRegistry(self.pairing, max_workers_per_owner=2)
        challenge = self.pairing.create_pairing_challenge("owner-a")
        self.credential = self.pairing.pair_device(challenge.code, "GPU worker")
        self.capabilities = ComputeWorkerCapabilities(
            worker_id=self.credential.device.device_id,
            protocol_version=1,
            paired=False,
            enabled=False,
            cpu_cores=16,
            ram_total_bytes=64 * 1024**3,
            ram_available_bytes=48 * 1024**3,
            storage_available_bytes=200 * 1024**3,
            backends=("ollama", "pytorch"),
            gpus=(GPUCapability("nvidia", "GPU", 24 * 1024**3, 20 * 1024**3, "pytorch"),),
            last_seen_epoch=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_registration_uses_pairing_credential_not_advertised_trust_flags(self):
        admitted = self.registry.register(
            owner_id="owner-a", credential=self.credential.token,
            capabilities=self.capabilities, now_epoch=100,
        )
        self.assertTrue(admitted.paired)
        self.assertTrue(admitted.enabled)
        self.assertEqual(admitted.last_seen_epoch, 100)

    def test_wrong_owner_cannot_register_device(self):
        with self.assertRaises(ComputeWorkerRegistrationError):
            self.registry.register(
                owner_id="owner-b", credential=self.credential.token,
                capabilities=self.capabilities, now_epoch=100,
            )

    def test_worker_id_must_match_paired_device(self):
        from dataclasses import replace
        with self.assertRaises(ComputeWorkerRegistrationError):
            self.registry.register(
                owner_id="owner-a", credential=self.credential.token,
                capabilities=replace(self.capabilities, worker_id="other-device"),
                now_epoch=100,
            )

    def test_revoked_credential_cannot_register(self):
        self.pairing.revoke_device("owner-a", self.credential.device.device_id)
        with self.assertRaises(ComputeWorkerRegistrationError):
            self.registry.register(
                owner_id="owner-a", credential=self.credential.token,
                capabilities=self.capabilities, now_epoch=100,
            )

    def test_owner_can_disable_worker_and_scheduler_excludes_it(self):
        self.registry.register(
            owner_id="owner-a", credential=self.credential.token,
            capabilities=self.capabilities, now_epoch=100,
        )
        self.assertTrue(self.registry.set_enabled(
            owner_id="owner-a", worker_id=self.credential.device.device_id, enabled=False
        ))
        record = self.registry.scheduler_records(owner_id="owner-a", now_epoch=101)[0]
        self.assertFalse(record["online"])

    def test_scheduler_records_are_owner_scoped(self):
        self.registry.register(
            owner_id="owner-a", credential=self.credential.token,
            capabilities=self.capabilities, now_epoch=100,
        )
        self.assertEqual(self.registry.scheduler_records(owner_id="owner-b", now_epoch=101), [])

    def test_revocation_from_registry_removes_worker(self):
        self.registry.register(
            owner_id="owner-a", credential=self.credential.token,
            capabilities=self.capabilities, now_epoch=100,
        )
        self.assertTrue(self.registry.revoke_from_registry(
            owner_id="owner-a", worker_id=self.credential.device.device_id
        ))
        self.assertEqual(self.registry.scheduler_records(owner_id="owner-a", now_epoch=101), [])


if __name__ == "__main__":
    unittest.main()
