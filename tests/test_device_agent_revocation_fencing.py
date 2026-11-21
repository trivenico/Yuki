import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from core.device_agent import DeviceAgentService
from core.device_pairing import DevicePairingService
from core.device_resource_reservations import DeviceResourceReservationService


class DeviceAgentRevocationFencingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(root / "auth.sqlite", password_iterations=100_000)
        self.auth.bootstrap_admin("owner", "a-secure-owner-password")
        self.session = self.auth.authenticate("owner", "a-secure-owner-password")
        self.pairing = DevicePairingService(root / "pairing.sqlite")
        challenge = self.pairing.create_pairing_challenge(self.session.user.user_id)
        self.credential = self.pairing.pair_device(challenge.code, "worker")
        self.reservations = DeviceResourceReservationService(
            root / "reservations.sqlite", self.auth, self.pairing,
        )
        self.agent = DeviceAgentService(
            root / "agent.sqlite", self.pairing, reservation_service=self.reservations,
        )

    def test_legacy_cpu_cores_do_not_publish_capacity_without_opt_in(self):
        result = self.agent.heartbeat(
            self.credential.token, sequence=1, status="idle",
            capabilities={
                "cpu_cores": 4, "ram_total_bytes": 8000,
                "ram_available_bytes": 5000, "gpus": [],
            },
        )
        self.assertEqual(result.sequence, 1)
        with self.assertRaises(Exception):
            self.reservations.reserve(
                self.session.token, self.credential.device.device_id, "no-precise-cpu",
                memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
                idempotency_key="no-precise-cpu",
                reservation_secret="no-precise-cpu-reservation-secret-long",
            )

    def test_revocation_invalidates_active_reservations(self):
        self.reservations.publish_capacity(
            self.credential.token, memory_bytes=5000,
            cpu_millicores=4000, gpu_memory_bytes=0, source_sequence=1,
        )
        reservation = self.reservations.reserve(
            self.session.token, self.credential.device.device_id, "revoke-task",
            memory_bytes=1000, cpu_millicores=500, gpu_memory_bytes=0,
            idempotency_key="revoke-task",
            reservation_secret="revocation-reservation-secret-with-entropy",
        )
        self.assertTrue(self.agent.revoke_device(
            self.session.user.user_id, self.credential.device.device_id
        ))
        with self.reservations._db() as db:
            row = db.execute(
                "SELECT status FROM resource_reservations WHERE reservation_id=?",
                (reservation.reservation_id,),
            ).fetchone()
        self.assertEqual(row["status"], "released")
        with self.assertRaises(PermissionError):
            self.reservations.publish_capacity(
                self.credential.token, memory_bytes=5000,
                cpu_millicores=4000, gpu_memory_bytes=0, source_sequence=2,
            )


if __name__ == "__main__":
    unittest.main()
