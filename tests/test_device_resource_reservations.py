import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.auth import BuiltInAuthService
from core.device_pairing import DevicePairingService
from core.device_agent import DeviceAgentService
from core.distributed_model_scheduler import DistributedModelScheduler, ModelRequirements
from core.device_resource_reservations import (
    CapacityUnavailable,
    DeviceResourceReservationService,
    ReservationConflict,
    StaleReservation,
)


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class AuthenticatedReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner.one", "A sufficiently secure password 1!")
        self.session = self.auth.authenticate("owner.one", "A sufficiently secure password 1!")
        self.auth.create_account("owner.two", "Another sufficiently secure password 2!")
        self.other_session = self.auth.authenticate("owner.two", "Another sufficiently secure password 2!")
        self.pairing = DevicePairingService(root / "pairing.sqlite")
        challenge = self.pairing.create_pairing_challenge(self.session.user.user_id)
        self.device = self.pairing.pair_device(challenge.code, "Yuki test node")
        self.clock = Clock()
        self.service = DeviceResourceReservationService(
            root / "reservations.sqlite", self.auth, self.pairing,
            lease_seconds=30, capacity_max_age_seconds=60, clock=self.clock,
        )
        self.service.publish_capacity(
            self.device.token, memory_bytes=1000, cpu_millicores=1500, gpu_memory_bytes=500
        )

    def reserve(self, task="task-a", key="key-a", secret=None, **overrides):
        args = dict(memory_bytes=400, cpu_millicores=500, gpu_memory_bytes=100)
        args.update(overrides)
        return self.service.reserve(
            self.session.token, self.device.device.device_id, task,
            idempotency_key=key,
            reservation_secret=secret or f"reservation-secret-for-{key}-with-high-entropy",
            **args,
        )

    def test_heartbeat_advances_reservation_watermark_before_capacity_refresh(self):
        agent = DeviceAgentService(
            Path(self.tmp.name) / "agent.sqlite",
            self.pairing,
            reservation_service=self.service,
        )
        agent.heartbeat(
            self.device.token,
            sequence=2,
            status="idle",
            capabilities={"cpu_cores": 2, "ram_total_bytes": 1000,
                          "ram_available_bytes": 800},
        )
        generation = self.service.publish_capacity(
            self.device.token,
            memory_bytes=2000,
            cpu_millicores=2000,
            gpu_memory_bytes=1000,
            source_sequence=1,
        )
        self.assertEqual(generation, 1)
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                self.session.token, self.device.device.device_id, "stale-heartbeat",
                memory_bytes=900, cpu_millicores=100,
                idempotency_key="stale-heartbeat",
                reservation_secret="stale-heartbeat-secret-with-enough-entropy",
            )
        fresh_generation = self.service.publish_capacity(
            self.device.token,
            memory_bytes=900,
            cpu_millicores=1200,
            gpu_memory_bytes=400,
            source_sequence=2,
        )
        self.assertEqual(fresh_generation, 2)

    def test_scheduler_reserves_capacity_before_reporting_placement(self):
        scheduler = DistributedModelScheduler()
        device_id = self.device.device.device_id
        decision, reservation = scheduler.reserve_plan(
            ModelRequirements("org/model", memory_bytes=400, cpu_cores=1,
                              gpu_memory_bytes=100),
            [{
                "device_id": device_id,
                "online": True,
                "status": "idle",
                "capabilities": {
                    "cpu_cores": 2,
                    "ram_available_bytes": 800,
                    "gpus": [{"memory_available_bytes": 400}],
                },
            }],
            reservation_service=self.service,
            session_token=self.session.token,
            task_id="scheduler-task",
            idempotency_key="scheduler-idempotency",
            reservation_secret="scheduler-reservation-secret-with-high-entropy",
        )
        self.assertEqual(decision.status, "reserved")
        self.assertEqual(decision.device_id, device_id)
        self.assertEqual(decision.reason, "capacity_reserved; dispatch_not_performed")
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation.cpu_millicores, 1000)
        self.assertEqual(reservation.memory_bytes, 400)
        self.assertEqual(reservation.gpu_memory_bytes, 100)
        self.assertEqual(reservation.status, "active")

    def test_scheduler_does_not_report_placement_when_capacity_is_unavailable(self):
        scheduler = DistributedModelScheduler()
        device_id = self.device.device.device_id
        decision, reservation = scheduler.reserve_plan(
            ModelRequirements("org/model", memory_bytes=900, cpu_cores=1),
            [{
                "device_id": device_id,
                "online": True,
                "status": "idle",
                "capabilities": {"cpu_cores": 2, "ram_available_bytes": 950},
            }],
            reservation_service=self.service,
            session_token=self.session.token,
            task_id="scheduler-overcapacity",
            idempotency_key="scheduler-overcapacity",
            reservation_secret="scheduler-overcapacity-secret-with-entropy",
        )
        self.assertEqual(decision.status, "unscheduled")
        self.assertIsNone(decision.device_id)
        self.assertIsNone(reservation)
        self.assertEqual(decision.reason, "eligible_devices_have_no_reservable_capacity")

    def test_capacity_source_sequence_rejects_out_of_order_updates(self):
        first_generation = self.service.publish_capacity(
            self.device.token, memory_bytes=900, cpu_millicores=1200,
            gpu_memory_bytes=400, source_sequence=10,
        )
        stale_generation = self.service.publish_capacity(
            self.device.token, memory_bytes=100, cpu_millicores=100,
            gpu_memory_bytes=0, source_sequence=9,
        )
        self.assertEqual(stale_generation, first_generation)
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                self.session.token, self.device.device.device_id, "sequence-check",
                memory_bytes=950, cpu_millicores=100,
                idempotency_key="sequence-check",
                reservation_secret="sequence-check-secret-with-enough-entropy",
            )
        newer_generation = self.service.publish_capacity(
            self.device.token, memory_bytes=1100, cpu_millicores=1400,
            gpu_memory_bytes=450, source_sequence=11,
        )
        self.assertEqual(newer_generation, first_generation + 1)

    def test_incomplete_heartbeat_watermark_blocks_delayed_capacity_publish(self):
        generation = self.service.publish_capacity(
            self.device.token, memory_bytes=900, cpu_millicores=1200,
            gpu_memory_bytes=400, source_sequence=10,
        )
        self.service.advance_capacity_sequence(
            self.device.token, source_sequence=11
        )
        stale_generation = self.service.publish_capacity(
            self.device.token, memory_bytes=2000, cpu_millicores=2000,
            gpu_memory_bytes=1000, source_sequence=10,
        )
        self.assertEqual(stale_generation, generation)
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                self.session.token, self.device.device.device_id, "watermark-check",
                memory_bytes=950, cpu_millicores=100,
                idempotency_key="watermark-check",
                reservation_secret="watermark-check-secret-with-enough-entropy",
            )

    def test_capacity_publication_requires_valid_paired_device_credential(self):
        with self.assertRaises(PermissionError):
            self.service.publish_capacity("not-a-device-token", memory_bytes=1, cpu_millicores=1)

    def test_capacity_is_bound_to_device_owner_and_device_id(self):
        other_challenge = self.pairing.create_pairing_challenge(self.other_session.user.user_id)
        other_device = self.pairing.pair_device(other_challenge.code, "Other node")
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                self.session.token, other_device.device.device_id, "task",
                memory_bytes=1, cpu_millicores=1, idempotency_key="other-device",
                reservation_secret="other-device-secret-with-enough-entropy",
            )

    def test_reservation_owner_is_derived_from_authenticated_session(self):
        with self.assertRaises(PermissionError):
            self.service.reserve(
                "forged-session", self.device.device.device_id, "task",
                memory_bytes=1, cpu_millicores=1, idempotency_key="forged",
                reservation_secret="forged-session-secret-with-enough-entropy",
            )
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                self.other_session.token, self.device.device.device_id, "task",
                memory_bytes=1, cpu_millicores=1, idempotency_key="cross-owner",
                reservation_secret="cross-owner-secret-with-enough-entropy",
            )

    def test_capacity_is_owner_scoped_to_authenticated_paired_device(self):
        other_challenge = self.pairing.create_pairing_challenge(self.other_session.user.user_id)
        other_device = self.pairing.pair_device(other_challenge.code, "Other node")
        self.service.publish_capacity(other_device.token, memory_bytes=10, cpu_millicores=10)
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                self.session.token, other_device.device.device_id, "task",
                memory_bytes=1, cpu_millicores=1, idempotency_key="cross",
                reservation_secret="cross-owner-secret-with-enough-entropy",
            )

    def test_reservation_accounts_for_memory_cpu_and_gpu(self):
        self.reserve()
        self.reserve(task="task-b", key="key-b", memory_bytes=600,
                     cpu_millicores=1000, gpu_memory_bytes=400)
        with self.assertRaises(CapacityUnavailable):
            self.reserve(task="task-c", key="key-c", memory_bytes=1)

    def test_concurrent_reservations_cannot_overcommit(self):
        barrier = threading.Barrier(3)
        outcomes = []
        lock = threading.Lock()

        def attempt(index):
            barrier.wait()
            try:
                self.reserve(task=f"task-{index}", key=f"key-{index}",
                             memory_bytes=600, cpu_millicores=800)
                outcome = "ok"
            except CapacityUnavailable:
                outcome = "full"
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ["ok", "full"])

    def test_stale_and_future_capacity_snapshots_fail_closed(self):
        self.clock.advance(61)
        with self.assertRaisesRegex(CapacityUnavailable, "capacity_snapshot_stale"):
            self.reserve()
        self.clock.value -= timedelta(seconds=100)
        with self.assertRaisesRegex(CapacityUnavailable, "capacity_snapshot_stale"):
            self.reserve()

    def test_lost_response_retry_recovers_same_active_reservation(self):
        first = self.reserve()
        recovered = self.reserve()
        self.assertEqual(recovered.reservation_id, first.reservation_id)
        self.assertEqual(recovered.fencing_token, first.fencing_token)
        self.assertEqual(recovered.token, first.token)
        self.assertNotIn(first.token, repr(recovered))

    def test_wrong_secret_and_changed_request_cannot_recover(self):
        self.reserve()
        with self.assertRaises(ReservationConflict):
            self.reserve(secret="different-secret-with-enough-entropy-123")
        with self.assertRaises(ReservationConflict):
            self.reserve(memory_bytes=401)

    def test_expired_reservation_is_not_resurrected(self):
        first = self.reserve()
        self.clock.advance(31)
        with self.assertRaises(StaleReservation):
            self.reserve()
        self.assertEqual(first.status, "active")

    def test_renew_and_release_require_secret_and_fencing_token(self):
        reservation = self.reserve()
        with self.assertRaises(StaleReservation):
            self.service.renew(reservation.reservation_id, "wrong-secret-value-with-sufficient-length",
                               fencing_token=reservation.fencing_token)
        with self.assertRaises(StaleReservation):
            self.service.renew(reservation.reservation_id, reservation.token,
                               fencing_token=reservation.fencing_token + 1)
        self.clock.advance(1)
        expiry = self.service.renew(reservation.reservation_id, reservation.token,
                                    fencing_token=reservation.fencing_token)
        self.assertGreater(datetime.fromisoformat(expiry), datetime.fromisoformat(reservation.expires_at))
        self.assertTrue(self.service.release(reservation.reservation_id, reservation.token,
                                             fencing_token=reservation.fencing_token))
        self.assertFalse(self.service.release(reservation.reservation_id, reservation.token,
                                              fencing_token=reservation.fencing_token))

    def test_fencing_tokens_increase_after_release(self):
        first = self.reserve()
        self.service.release(first.reservation_id, first.token, fencing_token=first.fencing_token)
        second = self.reserve(task="task-b", key="key-b")
        self.assertGreater(second.fencing_token, first.fencing_token)

    def test_idempotency_key_cannot_change_request_or_owner(self):
        first = self.reserve()
        with self.assertRaises(ReservationConflict):
            self.reserve(task="different-task")
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                self.other_session.token, self.device.device.device_id, "task-a",
                memory_bytes=400, cpu_millicores=500, gpu_memory_bytes=100,
                idempotency_key="key-a",
                reservation_secret="reservation-secret-for-key-a-with-high-entropy",
            )
        self.assertTrue(first.reservation_id)

    def test_secrets_are_hashed_not_persisted(self):
        reservation = self.reserve()
        db = sqlite3.connect(self.service.path)
        try:
            stored = db.execute(
                "SELECT token_hash FROM resource_reservations WHERE reservation_id=?",
                (reservation.reservation_id,),
            ).fetchone()[0]
        finally:
            db.close()
        self.assertNotEqual(stored, reservation.token)
        self.assertEqual(len(stored), 64)

    def test_validation_rejects_bool_and_short_secret(self):
        with self.assertRaisesRegex(ValueError, "invalid_cpu_millicores"):
            self.reserve(cpu_millicores=True)
        with self.assertRaisesRegex(ValueError, "invalid_reservation_secret"):
            self.reserve(secret="too-short")


if __name__ == "__main__":
    unittest.main()
