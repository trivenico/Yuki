import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


class DeviceResourceReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.service = DeviceResourceReservationService(
            Path(self.tmp.name) / "resources.sqlite",
            lease_seconds=30,
            capacity_max_age_seconds=60,
            clock=self.clock,
        )
        self.service.publish_capacity(
            "owner-a", "device-a", memory_bytes=1000,
            cpu_millicores=1500, gpu_memory_bytes=500,
        )

    def reserve(self, task="task-a", key="key-a", **overrides):
        args = dict(memory_bytes=400, cpu_millicores=500, gpu_memory_bytes=100)
        args.update(overrides)
        return self.service.reserve(
            "owner-a", "device-a", task, idempotency_key=key,
            reservation_secret=f"reservation-secret-for-{key}-with-high-entropy",
            **args
        )

    def test_reservation_accounts_for_all_resources(self):
        first = self.reserve()
        second = self.reserve(task="task-b", key="key-b",
                              memory_bytes=600, cpu_millicores=1000,
                              gpu_memory_bytes=400)
        self.assertEqual(first.status, "active")
        self.assertEqual(second.status, "active")
        with self.assertRaises(CapacityUnavailable):
            self.reserve(task="task-c", key="key-c", memory_bytes=1)

    def test_cpu_fraction_is_reserved_in_millicores(self):
        self.reserve(cpu_millicores=1000)
        with self.assertRaises(CapacityUnavailable):
            self.reserve(task="task-b", key="key-b", cpu_millicores=501)

    def test_concurrent_reservations_cannot_overcommit(self):
        barrier = threading.Barrier(3)
        outcomes = []
        lock = threading.Lock()

        def attempt(index):
            barrier.wait()
            try:
                result = self.reserve(
                    task=f"task-{index}", key=f"key-{index}",
                    memory_bytes=600, cpu_millicores=800,
                )
                value = ("ok", result.reservation_id)
            except CapacityUnavailable:
                value = ("full", None)
            with lock:
                outcomes.append(value)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(kind == "ok" for kind, _ in outcomes), 1)
        self.assertEqual(sum(kind == "full" for kind, _ in outcomes), 1)

    def test_stale_capacity_fails_closed(self):
        self.clock.advance(61)
        with self.assertRaisesRegex(CapacityUnavailable, "capacity_snapshot_stale"):
            self.reserve()

    def test_expired_lease_releases_capacity(self):
        first = self.reserve()
        self.clock.advance(31)
        second = self.reserve(task="task-b", key="key-b",
                              memory_bytes=1000, cpu_millicores=1500,
                              gpu_memory_bytes=500)
        self.assertNotEqual(first.reservation_id, second.reservation_id)

    def test_renew_and_release_require_token_and_fence(self):
        reservation = self.reserve()
        with self.assertRaises(StaleReservation):
            self.service.renew(reservation.reservation_id, "wrong-token-value-that-is-long-enough",
                               fencing_token=reservation.fencing_token)
        self.clock.advance(1)
        extended = self.service.renew(
            reservation.reservation_id, reservation.token,
            fencing_token=reservation.fencing_token,
        )
        self.assertGreater(datetime.fromisoformat(extended),
                           datetime.fromisoformat(reservation.expires_at))
        self.assertTrue(self.service.release(
            reservation.reservation_id, reservation.token,
            fencing_token=reservation.fencing_token,
        ))
        self.assertFalse(self.service.release(
            reservation.reservation_id, reservation.token,
            fencing_token=reservation.fencing_token,
        ))

    def test_fencing_tokens_increase_after_release(self):
        first = self.reserve()
        self.service.release(first.reservation_id, first.token,
                             fencing_token=first.fencing_token)
        second = self.reserve(task="task-b", key="key-b")
        self.assertGreater(second.fencing_token, first.fencing_token)

    def test_lost_response_retry_recovers_same_active_reservation(self):
        first = self.reserve()
        recovered = self.reserve()
        self.assertEqual(recovered.reservation_id, first.reservation_id)
        self.assertEqual(recovered.fencing_token, first.fencing_token)
        self.assertEqual(recovered.token, first.token)
        self.assertNotIn(first.token, repr(recovered))

    def test_idempotency_recovery_requires_original_secret(self):
        first = self.reserve()
        with self.assertRaisesRegex(ReservationConflict, "reservation_secret_mismatch"):
            self.service.reserve(
                "owner-a", "device-a", "task-a",
                memory_bytes=400, cpu_millicores=500, gpu_memory_bytes=100,
                idempotency_key="key-a",
                reservation_secret="different-secret-with-enough-entropy-123",
            )
        self.assertTrue(self.service.release(
            first.reservation_id, first.token, fencing_token=first.fencing_token
        ))

    def test_expired_idempotent_reservation_is_not_recovered(self):
        first = self.reserve()
        self.clock.advance(31)
        with self.assertRaises(StaleReservation):
            self.reserve()
        self.assertNotEqual(first.status, "expired")

    def test_short_reservation_secret_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_reservation_secret"):
            self.service.reserve(
                "owner-a", "device-a", "task-a", memory_bytes=1,
                cpu_millicores=1, idempotency_key="short-secret",
                reservation_secret="too-short",
            )

    def test_reservation_transition_rejects_non_ascii_token(self):
        reservation = self.reserve()
        with self.assertRaisesRegex(ValueError, "invalid_reservation_token"):
            self.service.renew(
                reservation.reservation_id,
                reservation.token + "é",
                fencing_token=reservation.fencing_token,
            )

    def test_idempotency_key_cannot_change_request(self):
        self.reserve()
        with self.assertRaises(ReservationConflict):
            self.reserve(task="different-task", key="key-a")

    def test_duplicate_active_task_is_rejected(self):
        self.reserve()
        with self.assertRaises(ReservationConflict):
            self.reserve(task="task-a", key="key-other")

    def test_validation_rejects_bool_and_bad_identifiers(self):
        with self.assertRaisesRegex(ValueError, "invalid_cpu_millicores"):
            self.reserve(cpu_millicores=True)
        with self.assertRaisesRegex(ValueError, "invalid_device_id"):
            self.service.publish_capacity(
                "owner-a", "../escape", memory_bytes=1,
                cpu_millicores=1,
            )

    def test_owner_isolation(self):
        with self.assertRaises(CapacityUnavailable):
            self.service.reserve(
                "owner-b", "device-a", "task-a", memory_bytes=1,
                cpu_millicores=1, idempotency_key="key-b",
                reservation_secret="owner-b-reservation-secret-with-high-entropy",
            )


if __name__ == "__main__":
    unittest.main()
