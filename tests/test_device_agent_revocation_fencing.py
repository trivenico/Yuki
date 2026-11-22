import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from core.device_agent import DeviceAgentService
from core.device_pairing import DevicePairingService
from core.device_resource_reservations import CapacityUnavailable, DeviceResourceReservationService


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


    def test_revocation_releases_expired_but_still_active_reservations(self):
        self.reservations.publish_capacity(
            self.credential.token, memory_bytes=5000,
            cpu_millicores=4000, gpu_memory_bytes=0, source_sequence=1,
        )
        reservation = self.reservations.reserve(
            self.session.token, self.credential.device.device_id, "expired-revoke-task",
            memory_bytes=1000, cpu_millicores=500, gpu_memory_bytes=0,
            idempotency_key="expired-revoke-task",
            reservation_secret="expired-revocation-reservation-secret",
        )
        with self.reservations._db() as db:
            db.execute(
                "UPDATE resource_reservations SET expires_at=? WHERE reservation_id=?",
                ("2000-01-01T00:00:00+00:00", reservation.reservation_id),
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

    def test_capacity_outbox_survives_ledger_publish_failure_and_retries(self):
        original = self.reservations._publish_capacity_for_device

        def fail_publish(*args, **kwargs):
            raise OSError("reservation ledger temporarily unavailable")

        self.reservations._publish_capacity_for_device = fail_publish
        try:
            with self.assertRaisesRegex(OSError, "temporarily unavailable"):
                self.agent.heartbeat(
                    self.credential.token, sequence=1, status="idle",
                    capabilities={
                        "ram_total_bytes": 8000, "ram_available_bytes": 5000,
                        "cpu_millicores": 2000, "gpus": [],
                    },
                )
        finally:
            self.reservations._publish_capacity_for_device = original

        with self.agent._connect() as db:
            state = db.execute(
                "SELECT sequence FROM device_agent_state WHERE device_id=?",
                (self.credential.device.device_id,),
            ).fetchone()
            outbox = db.execute(
                "SELECT sequence,memory_bytes,cpu_millicores,gpu_memory_bytes "
                "FROM device_capacity_outbox WHERE device_id=?",
                (self.credential.device.device_id,),
            ).fetchone()
        self.assertEqual(state["sequence"], 1)
        self.assertEqual(
            (outbox["sequence"], outbox["memory_bytes"], outbox["cpu_millicores"],
             outbox["gpu_memory_bytes"]),
            (1, 5000, 2000, 0),
        )

        self.assertTrue(self.agent.retry_pending_capacity(self.credential.token))
        with self.agent._connect() as db:
            remaining = db.execute(
                "SELECT 1 FROM device_capacity_outbox WHERE device_id=?",
                (self.credential.device.device_id,),
            ).fetchone()
        self.assertIsNone(remaining)
        with self.reservations._db() as db:
            capacity = db.execute(
                "SELECT source_sequence,memory_bytes,cpu_millicores,gpu_memory_bytes "
                "FROM resource_capacity WHERE owner_id=? AND device_id=?",
                (self.session.user.user_id, self.credential.device.device_id),
            ).fetchone()
        self.assertEqual(
            (capacity["source_sequence"], capacity["memory_bytes"],
             capacity["cpu_millicores"], capacity["gpu_memory_bytes"]),
            (1, 5000, 2000, 0),
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
        with self.assertRaises(CapacityUnavailable):
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
