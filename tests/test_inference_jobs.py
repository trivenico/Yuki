import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from core.device_pairing import DevicePairingService
from core.device_agent import DeviceAgentService
from core.auth import BuiltInAuthService
from core.device_resource_reservations import DeviceResourceReservationService, StaleReservation
from core.inference_jobs import InferenceJobService


class InferenceJobLeaseFencingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "yuki.sqlite"
        self.auth = BuiltInAuthService(str(Path(self.tmp.name) / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner", "A sufficiently secure password 1!")
        self.session = self.auth.authenticate("owner", "A sufficiently secure password 1!")
        self.owner_id = self.session.user.user_id
        self.pairing = DevicePairingService(self.database)
        challenge = self.pairing.create_pairing_challenge(self.owner_id)
        self.device = self.pairing.pair_device(challenge.code, "worker")
        self.reservations = DeviceResourceReservationService(
            Path(self.tmp.name) / "reservations.sqlite", self.auth, self.pairing,
        )
        self.reservations.publish_capacity(
            self.device.token, memory_bytes=10_000, cpu_millicores=10_000,
            gpu_memory_bytes=10_000,
        )
        self.jobs = InferenceJobService(
            self.database, self.pairing, reservation_service=self.reservations,
        )
        self.agent = DeviceAgentService(
            Path(self.tmp.name) / "agent.sqlite", self.pairing,
            reservation_service=self.reservations,
        )
        self.agent.inference_jobs = self.jobs
        self.job = self.jobs.create_job(
            self.owner_id, model_id="local/model",
            device_id=self.device.device.device_id, input_text="hello",
        )
        self.reservation = self.reservations.reserve(
            self.session.token, self.device.device.device_id, self.job.job_id,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            idempotency_key="job-reservation",
            reservation_secret="job-reservation-secret-with-sufficient-entropy",
        )
        self.jobs.bind_reservation(
            self.owner_id, self.job.job_id,
            reservation_id=self.reservation.reservation_id,
            fencing_token=self.reservation.fencing_token,
        )

    def _expire(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_jobs SET lease_expires_at=? WHERE job_id=?",
                ("2000-01-01T00:00:00+00:00", self.job.job_id),
            )
            db.commit()

    def _new_unreserved_job(self, text="saga test"):
        return self.jobs.create_job(
            self.owner_id, model_id="local/model",
            device_id=self.device.device.device_id, input_text=text,
        )

    def _age_reservation_operation(self, key):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_reservation_operations SET updated_at=? WHERE owner_id=? AND idempotency_key=?",
                ("2000-01-01T00:00:00+00:00", self.owner_id, key),
            )
            db.commit()

    def test_pending_cancellation_blocks_new_reservation_intent(self):
        key = "reserve-after-cancel"
        self.jobs.begin_reservation_operation(
            self.owner_id,self.job.job_id,
            idempotency_key=key,
            memory_bytes=0,cpu_millicores=100,gpu_memory_bytes=0,
        )
        stamp = "2999-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    created_at,updated_at,operation_type)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,'cancel')""",
                ("c" * 32,self.owner_id,self.job.job_id,self.device.device.device_id,
                 "cancel-" + self.job.job_id + "-admission",stamp,stamp),
            )
            db.commit()

        # The guard must run before the idempotency retry fast path too.
        with self.assertRaisesRegex(ValueError, "job_cancellation_pending"):
            self.jobs.begin_reservation_operation(
                self.owner_id,self.job.job_id,
                idempotency_key=key,
                memory_bytes=0,cpu_millicores=100,gpu_memory_bytes=0,
            )

    def test_cancelled_job_cannot_retry_pending_reservation_admission(self):
        key = "reserve-cancelled-job"
        self.jobs.begin_reservation_operation(
            self.owner_id,self.job.job_id,
            idempotency_key=key,
            memory_bytes=0,cpu_millicores=100,gpu_memory_bytes=0,
        )
        self.assertTrue(self.jobs.cancel_job(self.owner_id,self.job.job_id))
        with self.assertRaisesRegex(ValueError, "job_not_reservable"):
            self.jobs.begin_reservation_operation(
                self.owner_id,self.job.job_id,
                idempotency_key=key,
                memory_bytes=0,cpu_millicores=100,gpu_memory_bytes=0,
            )

    def test_pending_cancellation_blocks_reservation_binding(self):
        stamp = "2999-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,operation_type)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("d" * 32,self.owner_id,self.job.job_id,self.device.device.device_id,
                 "cancel-" + self.job.job_id + "-bind",
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp),
            )
            db.commit()

        with self.assertRaisesRegex(ValueError, "job_cancellation_pending"):
            self.jobs.bind_reservation(
                self.owner_id,self.job.job_id,
                reservation_id=self.reservation.reservation_id,
                fencing_token=self.reservation.fencing_token,
            )
        stored = self.jobs.get_job(self.owner_id,self.job.job_id)
        self.assertIsNone(stored.reservation_id)
        self.assertIsNone(stored.reservation_fencing_token)
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id,self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"],"active")

    def test_only_one_pending_reservation_intent_per_job(self):
        job = self._new_unreserved_job("single reservation intent")
        self.jobs.begin_reservation_operation(
            self.owner_id, job.job_id, idempotency_key="reserve-first",
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
        )
        with self.assertRaisesRegex(ValueError, "reservation_operation_pending"):
            self.jobs.begin_reservation_operation(
                self.owner_id, job.job_id, idempotency_key="reserve-second",
                memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            )
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute(
                "SELECT idempotency_key,state FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND operation_type='reserve'",
                (self.owner_id,job.job_id),
            ).fetchall()
        self.assertEqual(rows, [("reserve-first","pending")])

    def test_partial_reservation_binding_blocks_new_reservation_intent(self):
        job = self._new_unreserved_job("partial reservation binding")
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_jobs SET reservation_fencing_token=77 WHERE owner_id=? AND job_id=?",
                (self.owner_id,job.job_id),
            )
            db.commit()
        with self.assertRaisesRegex(ValueError, "job_already_reserved"):
            self.jobs.begin_reservation_operation(
                self.owner_id, job.job_id, idempotency_key="reserve-after-partial-bind",
                memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            )

    def test_orphan_cleanup_preserves_existing_reservation_binding(self):
        job = self._new_unreserved_job("binding cleanup race")
        reservation = self.reservations.reserve(
            self.session.token, self.device.device.device_id, job.job_id,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            idempotency_key="binding-cleanup-race",
            reservation_secret="binding-cleanup-race-secret-entropy",
        )
        self.jobs.bind_reservation(
            self.owner_id, job.job_id,
            reservation_id=reservation.reservation_id,
            fencing_token=reservation.fencing_token,
        )
        released = self.jobs.release_reservation_if_unbound(
            self.owner_id, job.job_id, self.device.device.device_id,
            reservation.reservation_id, reservation.fencing_token,
        )
        self.assertFalse(released)
        persisted = self.reservations.get_reservation_by_id(
            self.owner_id, reservation.reservation_id,
        )
        self.assertEqual(persisted["status"], "active")
        self.assertEqual(
            self.jobs.get_job(self.owner_id, job.job_id).reservation_id,
            reservation.reservation_id,
        )

    def test_pending_revocation_does_not_block_unrelated_device_worker(self):
        # Leave a stale reservation intent behind for device A, then defer A's
        # revocation. Device B must still be able to reconcile and claim its own
        # reservation-bound job.
        pending_job = self._new_unreserved_job("device A pending intent")
        self.jobs.begin_reservation_operation(
            self.owner_id, pending_job.job_id, idempotency_key="device-a-pending",
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
        )
        self._age_reservation_operation("device-a-pending")
        self.pairing.begin_device_revocation(
            self.owner_id, self.device.device.device_id,
        )

        challenge = self.pairing.create_pairing_challenge(self.owner_id)
        other = self.pairing.pair_device(challenge.code, "independent-worker")
        self.reservations.publish_capacity(
            other.token, memory_bytes=10_000, cpu_millicores=10_000,
            gpu_memory_bytes=10_000,
        )
        other_job = self.jobs.create_job(
            self.owner_id, model_id="local/model",
            device_id=other.device.device_id, input_text="independent work",
        )
        other_reservation = self.reservations.reserve(
            self.session.token, other.device.device_id, other_job.job_id,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            idempotency_key="device-b-reservation",
            reservation_secret="independent-reservation-secret-with-entropy",
        )
        self.jobs.bind_reservation(
            self.owner_id, other_job.job_id,
            reservation_id=other_reservation.reservation_id,
            fencing_token=other_reservation.fencing_token,
        )

        claimed = self.jobs.claim_next(other.token)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.job_id, other_job.job_id)
        with closing(sqlite3.connect(self.database)) as db:
            state = db.execute(
                "SELECT state FROM inference_reservation_operations WHERE idempotency_key=?",
                ("device-a-pending",),
            ).fetchone()[0]
        self.assertEqual(state, "pending")

    def test_pending_device_revocation_blocks_worker_claim(self):
        self.pairing.begin_device_revocation(
            self.owner_id, self.device.device.device_id,
        )
        with self.assertRaisesRegex(PermissionError, "device_revocation_pending"):
            self.jobs.claim_next(self.device.token)
        self.assertEqual(self.jobs.get_job(self.owner_id, self.job.job_id).status, "queued")

    def test_pending_device_revocation_blocks_worker_renewal_and_completion(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.pairing.begin_device_revocation(
            self.owner_id, self.device.device.device_id,
        )
        with self.assertRaisesRegex(PermissionError, "device_revocation_pending"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )
        with self.assertRaisesRegex(PermissionError, "device_revocation_pending"):
            self.jobs.complete_job(
                self.device.token, claimed.job_id,
                lease_token=claimed.lease_token, result_text="must not commit",
            )
        self.assertEqual(self.jobs.get_job(self.owner_id, claimed.job_id).status, "running")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")

    def test_reconciler_abandons_intent_when_ledger_write_never_happened(self):
        job = self._new_unreserved_job()
        key = "saga-no-ledger"
        self.jobs.begin_reservation_operation(
            self.owner_id, job.job_id, idempotency_key=key,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
        )
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 0, "abandoned": 1, "deferred": 0},
        )
        with closing(sqlite3.connect(self.database)) as db:
            state = db.execute(
                "SELECT state FROM inference_reservation_operations WHERE owner_id=? AND idempotency_key=?",
                (self.owner_id, key),
            ).fetchone()[0]
        self.assertEqual(state, "abandoned")

    def test_reconciler_releases_orphaned_active_reservation(self):
        job = self._new_unreserved_job()
        key = "saga-orphaned-ledger"
        self.jobs.begin_reservation_operation(
            self.owner_id, job.job_id, idempotency_key=key,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
        )
        reservation = self.reservations.reserve(
            self.session.token, job.device_id, job.job_id,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            idempotency_key=key,
            reservation_secret="orphaned-saga-reservation-secret-entropy",
        )
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 0, "abandoned": 1, "deferred": 0},
        )
        ledger = self.reservations.get_reservation_by_idempotency_key(self.owner_id, key)
        self.assertEqual(ledger["status"], "released")
        self.assertEqual(ledger["reservation_id"], reservation.reservation_id)
        self.assertNotIn("token", ledger)
        self.assertNotIn("token_hash", ledger)

    def test_reconciler_completes_binding_committed_before_journal_ack(self):
        job = self._new_unreserved_job()
        key = "saga-bound-before-ack"
        self.jobs.begin_reservation_operation(
            self.owner_id, job.job_id, idempotency_key=key,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
        )
        reservation = self.reservations.reserve(
            self.session.token, job.device_id, job.job_id,
            memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            idempotency_key=key,
            reservation_secret="bound-saga-reservation-secret-entropy",
        )
        self.jobs.bind_reservation(
            self.owner_id, job.job_id,
            reservation_id=reservation.reservation_id,
            fencing_token=reservation.fencing_token,
        )
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        with closing(sqlite3.connect(self.database)) as db:
            state = db.execute(
                "SELECT state FROM inference_reservation_operations WHERE owner_id=? AND idempotency_key=?",
                (self.owner_id, key),
            ).fetchone()[0]
        self.assertEqual(state, "completed")

    def test_recovery_prioritizes_pending_cancel_over_completion_intent(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.assertIsNotNone(claimed)
        stamp = "2000-01-01T00:00:00+00:00"
        lease_hash = __import__("hashlib").sha256(
            claimed.lease_token.encode("ascii"),
        ).hexdigest()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,
                    operation_type,target_status,result_text,error_code,lease_token_hash)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'complete','succeeded',?,NULL,?)""",
                ("f" * 32,self.owner_id,self.job.job_id,self.device.device.device_id,
                 "system-complete-" + self.job.job_id,
                 self.reservation.reservation_id,self.reservation.fencing_token,
                 stamp,stamp,"worker result",lease_hash),
            )
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,operation_type)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("a" * 32,self.owner_id,self.job.job_id,self.device.device.device_id,
                 "cancel-" + self.job.job_id + "-recovery-precedence",
                 self.reservation.reservation_id,self.reservation.fencing_token,
                 stamp,stamp),
            )
            db.commit()

        result = self.jobs.reconcile_reservation_operations(stale_after_seconds=1)
        self.assertEqual(result, {"completed": 1, "abandoned": 1, "deferred": 0})
        stored = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual(stored.status, "cancelled")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")

    def test_pending_owner_cancellation_fences_worker_completion(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.assertIsNotNone(claimed)
        stamp = "2999-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,operation_type)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("e" * 32,self.owner_id,self.job.job_id,self.device.device.device_id,
                 "cancel-" + self.job.job_id + "-completion-fence",
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp),
            )
            db.commit()

        with self.assertRaisesRegex(ValueError, "job_cancellation_pending"):
            self.jobs.complete_job(
                self.device.token, claimed.job_id,
                lease_token=claimed.lease_token, result_text="must not win cancellation",
            )

        stored = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual(stored.status, "running")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")

    def test_worker_claim_racing_owner_cancellation_has_one_safe_outcome(self):
        barrier = Barrier(2)

        def claim():
            barrier.wait(timeout=5)
            return self.jobs.claim_next(self.device.token)

        def cancel():
            barrier.wait(timeout=5)
            return self.jobs.cancel_job(self.owner_id, self.job.job_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claim_future = pool.submit(claim)
            cancel_future = pool.submit(cancel)
            claimed = claim_future.result(timeout=10)
            cancelled = cancel_future.result(timeout=10)

        self.assertTrue(cancelled)
        final = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual(final.status, "cancelled")
        self.assertIsNone(final.lease_expires_at)
        self.assertIsNone(final.lease_token)
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")
        if claimed is not None:
            # Claim linearized first: cancellation must invalidate its lease
            # before the worker can successfully complete the job.
            with self.assertRaisesRegex(ValueError, "not_owned"):
                self.jobs.complete_job(
                    self.device.token, claimed.job_id,
                    lease_token=claimed.lease_token, result_text="late result",
                )
        with closing(sqlite3.connect(self.database)) as db:
            pending_cancels = db.execute(
                """SELECT COUNT(*) FROM inference_reservation_operations
                   WHERE owner_id=? AND job_id=? AND operation_type='cancel'
                     AND state='pending'""",
                (self.owner_id, self.job.job_id),
            ).fetchone()[0]
        self.assertEqual(pending_cancels, 0)

    def test_concurrent_worker_polls_claim_a_job_exactly_once(self):
        workers = 8
        barrier = Barrier(workers)

        def poll():
            barrier.wait(timeout=5)
            return self.jobs.claim_next(self.device.token)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda _index: poll(), range(workers)))

        claims = [item for item in results if item is not None]
        self.assertEqual(len(claims), 1)
        self.assertTrue(claims[0].lease_token)
        stored = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual(stored.status, "running")
        self.assertIsNone(stored.lease_token)

    def test_unbound_job_is_never_claimed(self):
        self.jobs.cancel_job(self.owner_id, self.job.job_id)
        unbound = self.jobs.create_job(
            self.owner_id, model_id="local/model",
            device_id=self.device.device.device_id, input_text="must not execute",
        )
        self.assertIsNone(self.jobs.claim_next(self.device.token))
        self.assertEqual(self.jobs.get_job(self.owner_id, unbound.job_id).status, "queued")

    def test_released_reservation_prevents_claim(self):
        self.reservations.release(
            self.device.token, self.reservation.reservation_id,
            self.reservation.token, fencing_token=self.reservation.fencing_token,
        )
        self.assertIsNone(self.jobs.claim_next(self.device.token))
        self.assertEqual(self.jobs.get_job(self.owner_id, self.job.job_id).status, "failed")
        self.assertEqual(self.jobs.get_job(self.owner_id, self.job.job_id).error_code, "reservation_invalid")

    def test_expired_reservation_prevents_claim(self):
        with closing(sqlite3.connect(self.reservations.path)) as db:
            db.execute(
                "UPDATE resource_reservations SET expires_at=? WHERE reservation_id=?",
                ("2000-01-01T00:00:00+00:00", self.reservation.reservation_id),
            )
            db.commit()
        self.assertIsNone(self.jobs.claim_next(self.device.token))
        self.assertEqual(self.jobs.get_job(self.owner_id, self.job.job_id).status, "failed")
        self.assertEqual(self.jobs.get_job(self.owner_id, self.job.job_id).error_code, "reservation_invalid")

    def test_reservation_revocation_fences_renewal_and_completion(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.reservations.release(
            self.device.token, self.reservation.reservation_id,
            self.reservation.token, fencing_token=self.reservation.fencing_token,
        )
        with self.assertRaisesRegex(ValueError, "fence_not_active"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )
        with self.assertRaisesRegex(ValueError, "fence_not_active"):
            self.jobs.complete_job(
                self.device.token, claimed.job_id,
                lease_token=claimed.lease_token, result_text="must not commit",
            )
        self.assertEqual(self.jobs.get_job(self.owner_id, claimed.job_id).status, "running")

    def test_pending_completion_takes_precedence_over_expired_lease_recovery(self):
        claimed = self.jobs.claim_next(self.device.token)
        original_release = self.reservations.release_active_reservation

        def interrupt_before_ledger_release(*args, **kwargs):
            raise RuntimeError("simulated completion interruption")

        self.reservations.release_active_reservation = interrupt_before_ledger_release
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated completion interruption"):
                self.jobs.complete_job(
                    self.device.token, claimed.job_id,
                    lease_token=claimed.lease_token, result_text="durable result",
                )
        finally:
            self.reservations.release_active_reservation = original_release

        self._expire()
        self._age_reservation_operation("system-complete-" + claimed.job_id)

        recovered = self.jobs.recover_expired_worker_leases(stale_after_seconds=1)

        self.assertEqual(recovered, {"completed": 1, "abandoned": 0, "deferred": 0})
        job = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result_text, "durable result")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")
        with closing(sqlite3.connect(self.database)) as db:
            expiry = db.execute(
                "SELECT 1 FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND idempotency_key=?",
                (self.owner_id, claimed.job_id, "system-expire-" + claimed.job_id),
            ).fetchone()
        self.assertIsNone(expiry)

    def test_owner_cannot_claim_internal_expiry_idempotency_key(self):
        job = self._new_unreserved_job("reserved system key")
        with self.assertRaisesRegex(ValueError, "reserved_idempotency_key"):
            self.jobs.begin_reservation_operation(
                self.owner_id, job.job_id,
                idempotency_key="system-expire-" + job.job_id,
                memory_bytes=100, cpu_millicores=100, gpu_memory_bytes=0,
            )

    def test_pending_cancellation_takes_precedence_over_expired_lease_recovery(self):
        claimed = self.jobs.claim_next(self.device.token)
        original_release = self.reservations.release_active_reservation

        def commit_ledger_then_crash(*args, **kwargs):
            original_release(*args, **kwargs)
            raise RuntimeError("simulated cancellation interruption")

        self.reservations.release_active_reservation = commit_ledger_then_crash
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated cancellation interruption"):
                self.jobs.cancel_job(self.owner_id, claimed.job_id)
        finally:
            self.reservations.release_active_reservation = original_release

        self._expire()
        with closing(sqlite3.connect(self.database)) as db:
            key = db.execute(
                "SELECT idempotency_key FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND operation_type='cancel' AND state='pending'",
                (self.owner_id, claimed.job_id),
            ).fetchone()[0]
        self._age_reservation_operation(key)

        recovered = self.jobs.recover_expired_worker_leases(stale_after_seconds=1)

        self.assertEqual(recovered, {"completed": 1, "abandoned": 0, "deferred": 0})
        job = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(job.status, "cancelled")
        self.assertIsNone(job.lease_expires_at)
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")
        with closing(sqlite3.connect(self.database)) as db:
            expiry = db.execute(
                "SELECT 1 FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND idempotency_key=?",
                (self.owner_id, claimed.job_id, "system-expire-" + claimed.job_id),
            ).fetchone()
        self.assertIsNone(expiry)

    def test_expired_lease_recovery_drains_multiple_bounded_batches(self):
        now = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            template = db.execute(
                "SELECT owner_id,model_id,device_id,input_text,reservation_id,"
                "reservation_fencing_token FROM inference_jobs WHERE job_id=?",
                (self.job.job_id,),
            ).fetchone()
            rows = [
                (f"{index:032x}", template[0], template[1], template[2],
                 "running", f"expired batch {index}", now, now, now,
                 "0" * 64, template[4], template[5])
                for index in range(1, 1002)
            ]
            db.executemany(
                """INSERT INTO inference_jobs
                   (job_id,owner_id,model_id,device_id,status,input_text,created_at,
                    updated_at,lease_expires_at,lease_token_hash,reservation_id,
                    reservation_fencing_token)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            db.commit()

        recovered = self.jobs.recover_expired_worker_leases(stale_after_seconds=1)
        self.assertEqual(recovered["deferred"], 0)
        self.assertEqual(recovered["abandoned"], 1001)
        with closing(sqlite3.connect(self.database)) as db:
            remaining = db.execute(
                "SELECT COUNT(*) FROM inference_jobs AS job WHERE job.status='running' "
                "AND job.lease_expires_at<=? AND NOT EXISTS ("
                "SELECT 1 FROM inference_reservation_operations AS op "
                "WHERE op.owner_id=job.owner_id AND op.job_id=job.job_id "
                "AND op.state='pending' AND op.operation_type IN ('complete','cancel'))",
                (now,),
            ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_expired_lease_is_terminal_and_never_replayed(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.assertIsNotNone(claimed.lease_token)
        self._expire()

        self.assertIsNone(self.jobs.claim_next(self.device.token))
        expired = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual(expired.status, "failed")
        self.assertEqual(expired.error_code, "lease_expired")
        self.assertIsNone(expired.lease_expires_at)
        released = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(released["status"], "released")
        with closing(sqlite3.connect(self.database)) as db:
            recovery = db.execute(
                "SELECT state,operation_type,target_status,error_code FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND idempotency_key=?",
                (self.owner_id,self.job.job_id,"system-expire-"+self.job.job_id),
            ).fetchone()
        self.assertEqual(tuple(recovery),("completed","complete","failed","lease_expired"))

        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token=claimed.lease_token, result_text="stale",
            )
        self.assertIsNone(self.jobs.claim_next(self.device.token))

    def test_expired_lease_intent_survives_ledger_commit_before_jobs_commit_crash(self):
        claimed = self.jobs.claim_next(self.device.token)
        self._expire()
        original_release = self.reservations.release_active_reservation

        def commit_ledger_then_crash(*args, **kwargs):
            original_release(*args, **kwargs)
            raise RuntimeError("simulated expiry recovery crash")

        self.reservations.release_active_reservation = commit_ledger_then_crash
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated expiry recovery crash"):
                self.jobs.recover_expired_worker_leases()
        finally:
            self.reservations.release_active_reservation = original_release

        self.assertEqual(self.jobs.get_job(self.owner_id, claimed.job_id).status, "running")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")
        with closing(sqlite3.connect(self.database)) as db:
            operation = db.execute(
                "SELECT state,operation_type,target_status,error_code FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND idempotency_key=?",
                (self.owner_id, claimed.job_id, "system-expire-" + claimed.job_id),
            ).fetchone()
        self.assertEqual(tuple(operation), ("pending", "complete", "failed", "lease_expired"))

        recovered = self.jobs.recover_expired_worker_leases()
        self.assertEqual(recovered, {"completed": 1, "abandoned": 0, "deferred": 0})
        job = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_code, "lease_expired")
        self.assertIsNone(job.lease_expires_at)

    def test_renewal_rejects_partial_reservation_binding_without_touching_ledger(self):
        claimed = self.jobs.claim_next(self.device.token)
        for column, value in (
            ("reservation_fencing_token", None),
            ("reservation_id", None),
        ):
            with self.subTest(column=column):
                with closing(sqlite3.connect(self.database)) as db:
                    db.execute(
                        f"UPDATE inference_jobs SET {column}=? WHERE job_id=?",
                        (value, claimed.job_id),
                    )
                    db.commit()
                with self.assertRaisesRegex(ValueError, "reservation_binding_invalid"):
                    self.jobs.renew_job_lease(
                        self.device.token, claimed.job_id, lease_token=claimed.lease_token,
                    )
                with closing(sqlite3.connect(self.database)) as db:
                    db.execute(
                        f"UPDATE inference_jobs SET {column}=? WHERE job_id=?",
                        (
                            self.reservation.fencing_token if column == "reservation_fencing_token"
                            else self.reservation.reservation_id,
                            claimed.job_id,
                        ),
                    )
                    db.commit()
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")
        self.assertEqual(
            self.jobs.get_job(self.owner_id, claimed.job_id).lease_expires_at,
            claimed.lease_expires_at,
        )

    def test_renewal_rejects_fully_missing_reservation_binding(self):
        claimed = self.jobs.claim_next(self.device.token)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_jobs SET reservation_id=NULL, "
                "reservation_fencing_token=NULL WHERE job_id=?",
                (claimed.job_id,),
            )
            db.commit()

        with self.assertRaisesRegex(ValueError, "reservation_binding_invalid"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )

        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")
        job = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.lease_expires_at, claimed.lease_expires_at)

    def test_live_claim_renews_job_and_reservation_leases_together(self):
        claimed = self.jobs.claim_next(self.device.token)
        original_expiry = claimed.lease_expires_at
        original_reservation = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )

        renewed = self.jobs.renew_job_lease(
            self.device.token, claimed.job_id, lease_token=claimed.lease_token,
        )

        self.assertEqual(renewed.status, "running")
        self.assertEqual(renewed.lease_token, claimed.lease_token)
        self.assertGreater(renewed.lease_expires_at, original_expiry)
        self.assertEqual(
            self.jobs.get_job(self.owner_id, claimed.job_id).lease_expires_at,
            renewed.lease_expires_at,
        )
        renewed_reservation = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(renewed_reservation["expires_at"], renewed.lease_expires_at)
        self.assertGreater(renewed_reservation["expires_at"], original_reservation["expires_at"])

    def test_expired_claim_cannot_be_renewed_or_resurrected(self):
        claimed = self.jobs.claim_next(self.device.token)
        self._expire()
        with self.assertRaisesRegex(ValueError, "lease_expired_or_not_owned"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )
        self.assertIsNone(self.jobs.claim_next(self.device.token))
        job = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_code, "lease_expired")

    def test_wrong_token_cannot_renew_claim(self):
        claimed = self.jobs.claim_next(self.device.token)
        with self.assertRaisesRegex(ValueError, "lease_expired_or_not_owned"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token="z" * 43,
            )
        self.assertEqual(
            self.jobs.get_job(self.owner_id, claimed.job_id).lease_expires_at,
            claimed.lease_expires_at,
        )

    def test_revoked_device_cannot_renew_claim(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.pairing.revoke_device(self.owner_id, self.device.device.device_id)
        with self.assertRaisesRegex(PermissionError, "invalid_device_credential"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )

    def test_cancel_rejects_partial_reservation_binding_and_recovery_fails_closed(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_jobs SET reservation_fencing_token=NULL WHERE job_id=?",
                (self.job.job_id,),
            )
            db.commit()

        with self.assertRaisesRegex(ValueError, "reservation_binding_invalid"):
            self.jobs.cancel_job(self.owner_id, self.job.job_id)

        job = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual(job.status, "queued")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")

        with closing(sqlite3.connect(self.database)) as db:
            pending = db.execute(
                "SELECT state FROM inference_reservation_operations "
                "WHERE owner_id=? AND job_id=? AND operation_type='cancel'",
                (self.owner_id, self.job.job_id),
            ).fetchone()
            db.execute(
                "UPDATE inference_reservation_operations SET updated_at=? "
                "WHERE owner_id=? AND job_id=? AND operation_type='cancel'",
                ("2000-01-01T00:00:00+00:00", self.owner_id, self.job.job_id),
            )
            db.commit()
        self.assertEqual(pending, ("pending",))

        result = self.jobs.reconcile_reservation_operations(stale_after_seconds=1)
        self.assertEqual(result["abandoned"], 1)
        job = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual((job.status, job.error_code), ("failed", "reservation_invalid"))
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")

    def test_cancel_recovery_rejects_missing_reservation_id_with_fence(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_jobs SET reservation_id=NULL WHERE job_id=?",
                (self.job.job_id,),
            )
            db.commit()

        with self.assertRaisesRegex(ValueError, "reservation_binding_invalid"):
            self.jobs.cancel_job(self.owner_id, self.job.job_id)

        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_reservation_operations SET updated_at=? "
                "WHERE owner_id=? AND job_id=? AND operation_type='cancel'",
                ("2000-01-01T00:00:00+00:00", self.owner_id, self.job.job_id),
            )
            db.commit()

        result = self.jobs.reconcile_reservation_operations(stale_after_seconds=1)
        self.assertEqual(result["abandoned"], 1)
        job = self.jobs.get_job(self.owner_id, self.job.job_id)
        self.assertEqual((job.status, job.error_code), ("failed", "reservation_invalid"))
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")

    def test_cancel_fails_closed_without_reservation_service_for_bound_job(self):
        jobs_without_ledger = InferenceJobService(self.database, self.pairing)

        with self.assertRaisesRegex(RuntimeError, "reservation_service_required"):
            jobs_without_ledger.cancel_job(self.owner_id, self.job.job_id)

        job = jobs_without_ledger.get_job(self.owner_id, self.job.job_id)
        self.assertEqual(job.status, "queued")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")

    def test_fresh_pending_cancel_intent_blocks_worker_claim(self):
        stamp = "2999-01-01T00:00:00+00:00"
        key = "cancel-" + self.job.job_id + "-fresh"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,operation_type)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("f" * 32,self.owner_id,self.job.job_id,self.device.device.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp),
            )
            db.commit()

        self.assertIsNone(self.jobs.claim_next(self.device.token))
        job = self.jobs.get_job(self.owner_id,self.job.job_id)
        self.assertEqual(job.status,"queued")
        self.assertIsNone(job.lease_token)

    def test_pending_reservation_operation_probe_detects_fresh_intent(self):
        stamp = "2999-01-01T00:00:00+00:00"
        key = "cancel-" + self.job.job_id + "-startup"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,operation_type)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("a" * 32,self.owner_id,self.job.job_id,self.device.device.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp),
            )
            db.commit()

        self.assertTrue(self.jobs.has_pending_reservation_operations())

    def test_cancel_retry_reuses_existing_pending_operation(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "cancel-" + claimed.job_id + "-existing"
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,operation_type)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("e" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp),
            )
            db.commit()

        self.assertTrue(self.jobs.cancel_job(self.owner_id, claimed.job_id))

        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute(
                "SELECT idempotency_key,state FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND operation_type='cancel'",
                (self.owner_id,claimed.job_id),
            ).fetchall()
        self.assertEqual(rows, [(key,"completed")])
        self.assertEqual(self.jobs.get_job(self.owner_id,claimed.job_id).status,"cancelled")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id,self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"],"released")

    def test_reconciler_finishes_cancel_after_ledger_release_crash_window(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "cancel-" + claimed.job_id
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO inference_reservation_operations
                (operation_id,owner_id,job_id,device_id,idempotency_key,
                 memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                 reservation_id,fencing_token,created_at,updated_at,operation_type)
                VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("c" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp))
            db.commit()
        self.reservations.release(
            self.device.token, self.reservation.reservation_id,
            self.reservation.token, fencing_token=self.reservation.fencing_token,
        )
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        cancelled = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(cancelled.lease_expires_at)

    def test_cancel_terminalizes_expired_active_reservation(self):
        with closing(sqlite3.connect(self.reservations.path)) as db:
            db.execute(
                "UPDATE resource_reservations SET expires_at=? WHERE reservation_id=?",
                ("2000-01-01T00:00:00+00:00", self.reservation.reservation_id),
            )
            db.commit()

        self.assertTrue(self.jobs.cancel_job(self.owner_id, self.job.job_id))
        self.assertEqual(
            self.jobs.get_job(self.owner_id, self.job.job_id).status, "cancelled",
        )
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "expired")

    def test_reconciler_releases_reservation_for_pending_cancel(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "cancel-" + claimed.job_id
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO inference_reservation_operations
                (operation_id,owner_id,job_id,device_id,idempotency_key,
                 memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                 reservation_id,fencing_token,created_at,updated_at,operation_type)
                VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
                ("d" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp))
            db.commit()
        with closing(sqlite3.connect(self.reservations.path)) as ledger_db:
            ledger_db.execute(
                "UPDATE resource_reservations SET expires_at=? WHERE reservation_id=?",
                ("2000-01-01T00:00:00+00:00", self.reservation.reservation_id),
            )
            ledger_db.commit()
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        ledger = self.reservations.get_reservation_by_id(self.owner_id,self.reservation.reservation_id)
        self.assertEqual(ledger["status"], "expired")
        self.assertEqual(self.jobs.get_job(self.owner_id,claimed.job_id).status,"cancelled")

    def test_cancellation_racing_completion_leaves_one_terminal_job(self):
        claimed = self.jobs.claim_next(self.device.token)
        barrier = Barrier(2)

        def complete():
            barrier.wait(timeout=5)
            try:
                return self.jobs.complete_job(
                    self.device.token, claimed.job_id,
                    lease_token=claimed.lease_token, result_text="race-result",
                ).status
            except ValueError:
                return "rejected"

        def cancel():
            barrier.wait(timeout=5)
            return self.jobs.cancel_job(self.owner_id, claimed.job_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            completed = pool.submit(complete)
            cancelled = pool.submit(cancel)
            completion_result = completed.result(timeout=10)
            cancellation_result = cancelled.result(timeout=10)

        final = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertIn(final.status, ("succeeded", "cancelled"))
        self.assertIn(completion_result, ("succeeded", "rejected"))
        if final.status == "succeeded":
            self.assertEqual(final.result_text, "race-result")
            with closing(sqlite3.connect(self.database)) as db:
                cancel_state = db.execute(
                    "SELECT state FROM inference_reservation_operations WHERE job_id=? AND operation_type='cancel' ORDER BY created_at DESC LIMIT 1",
                    (claimed.job_id,),
                ).fetchone()
            if cancel_state is not None:
                self.assertEqual(cancel_state[0], "abandoned")
        else:
            self.assertIsNone(final.result_text)
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "released",
        )
        self.assertIsNone(final.lease_expires_at)
        with closing(sqlite3.connect(self.database)) as db:
            pending = db.execute(
                """SELECT COUNT(*) FROM inference_reservation_operations
                   WHERE job_id=? AND state='pending'""",
                (claimed.job_id,),
            ).fetchone()[0]
        self.assertEqual(pending, 0)

    def test_cancellation_racing_device_revocation_never_leaves_live_capacity(self):
        claimed = self.jobs.claim_next(self.device.token)
        device_id = self.device.device.device_id
        barrier = Barrier(2)

        def cancel():
            barrier.wait(timeout=5)
            return self.jobs.cancel_job(self.owner_id, claimed.job_id)

        def revoke():
            barrier.wait(timeout=5)
            return self.agent.revoke_device(self.owner_id, device_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            cancelled = pool.submit(cancel)
            revoked = pool.submit(revoke)
            cancel_result = cancelled.result(timeout=10)
            revoke_result = revoked.result(timeout=10)

        self.assertTrue(revoke_result)
        final = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertIn(final.status, ("cancelled", "failed"))
        if final.status == "failed":
            self.assertEqual(final.error_code, "device_revoked")
        self.assertIsNone(final.lease_expires_at)
        self.assertIsNone(final.lease_token)
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "released",
        )
        self.assertEqual(self.pairing.pending_device_revocations(), [])

    def test_cancel_revokes_inflight_lease_token(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.assertTrue(self.jobs.cancel_job(self.owner_id, self.job.job_id))
        with closing(sqlite3.connect(self.reservations.path)) as db:
            status = db.execute(
                "SELECT status FROM resource_reservations WHERE reservation_id=?",
                (self.reservation.reservation_id,),
            ).fetchone()[0]
        self.assertEqual(status, "released")
        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token=claimed.lease_token, result_text="late",
            )
        self.assertEqual(self.jobs.get_job(self.owner_id, self.job.job_id).status, "cancelled")

    def test_completion_terminalizes_expired_active_reservation(self):
        claimed = self.jobs.claim_next(self.device.token)
        with closing(sqlite3.connect(self.reservations.path)) as db:
            db.execute(
                "UPDATE resource_reservations SET expires_at=? WHERE reservation_id=?",
                ("2000-01-01T00:00:00+00:00", self.reservation.reservation_id),
            )
            db.commit()

        completed = self.jobs.complete_job(
            self.device.token, claimed.job_id,
            lease_token=claimed.lease_token, result_text="completed",
        )
        self.assertEqual(completed.status, "succeeded")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "expired")

    def test_completion_releases_reservation_and_clears_worker_lease(self):
        claimed = self.jobs.claim_next(self.device.token)
        completed = self.jobs.complete_job(
            self.device.token, claimed.job_id,
            lease_token=claimed.lease_token, result_text="completed",
        )
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(completed.result_text, "completed")
        self.assertIsNone(completed.lease_expires_at)
        retried = self.jobs.complete_job(
            self.device.token, claimed.job_id,
            lease_token=claimed.lease_token, result_text="completed",
        )
        self.assertEqual(retried.status, "succeeded")
        self.assertEqual(retried.result_text, "completed")
        with self.assertRaisesRegex(ValueError, "not_replayable"):
            self.jobs.complete_job(
                self.device.token, claimed.job_id,
                lease_token="A" * 40, result_text="completed",
            )
        with self.assertRaisesRegex(ValueError, "idempotency_conflict"):
            self.jobs.complete_job(
                self.device.token, claimed.job_id,
                lease_token=claimed.lease_token, result_text="different result",
            )
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")
        with closing(sqlite3.connect(self.database)) as db:
            state = db.execute(
                "SELECT state FROM inference_reservation_operations WHERE owner_id=? AND idempotency_key=?",
                (self.owner_id, "system-complete-" + claimed.job_id),
            ).fetchone()[0]
        self.assertEqual(state, "completed")


    def test_completion_intent_survives_ledger_commit_before_jobs_commit_crash(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "system-complete-" + claimed.job_id
        original_release = self.reservations.release_active_reservation

        def commit_ledger_then_crash(*args, **kwargs):
            original_release(*args, **kwargs)
            raise RuntimeError("simulated process loss after ledger commit")

        self.reservations.release_active_reservation = commit_ledger_then_crash
        try:
            with self.assertRaisesRegex(
                RuntimeError, "simulated process loss after ledger commit"
            ):
                self.jobs.complete_job(
                    self.device.token, claimed.job_id,
                    lease_token=claimed.lease_token, result_text="durable-result",
                )
        finally:
            self.reservations.release_active_reservation = original_release

        # The ledger committed, while the second jobs transaction rolled back.
        # The separately committed phase-one journal must still be recoverable.
        self.assertEqual(self.jobs.get_job(self.owner_id, claimed.job_id).status, "running")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")
        with closing(sqlite3.connect(self.database)) as db:
            operation = db.execute(
                "SELECT state,operation_type,target_status,result_text FROM inference_reservation_operations WHERE owner_id=? AND idempotency_key=?",
                (self.owner_id, key),
            ).fetchone()
        self.assertEqual(tuple(operation), ("pending", "complete", "succeeded", "durable-result"))

        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        recovered = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(recovered.result_text, "durable-result")
        self.assertIsNone(recovered.lease_expires_at)

    def test_completion_error_releases_reservation(self):
        claimed = self.jobs.claim_next(self.device.token)
        failed = self.jobs.complete_job(
            self.device.token, claimed.job_id,
            lease_token=claimed.lease_token, error_code="worker_failed",
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "worker_failed")
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "released",
        )

    def test_reconciler_defers_stale_completion_fence_without_aborting_batch(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "system-complete-" + claimed.job_id
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO inference_reservation_operations
                (operation_id,owner_id,job_id,device_id,idempotency_key,
                 memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                 reservation_id,fencing_token,created_at,updated_at,operation_type,
                 target_status,result_text,error_code)
                VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'complete','failed',NULL,'worker_failed')""",
                ("a" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp))
            db.commit()
        self._age_reservation_operation(key)
        original_release = self.reservations.release_active_reservation

        def stale_once(*args, **kwargs):
            raise StaleReservation("transient fence race")

        self.reservations.release_active_reservation = stale_once
        try:
            self.assertEqual(
                self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
                {"completed": 0, "abandoned": 0, "deferred": 1},
            )
        finally:
            self.reservations.release_active_reservation = original_release

        self.assertEqual(self.jobs.get_job(self.owner_id, claimed.job_id).status, "running")
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "active",
        )
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        recovered = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(recovered.status, "failed")
        self.assertEqual(recovered.error_code, "worker_failed")

    def test_concurrent_reconcilers_process_completion_once(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "system-complete-" + claimed.job_id
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO inference_reservation_operations
                (operation_id,owner_id,job_id,device_id,idempotency_key,
                 memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                 reservation_id,fencing_token,created_at,updated_at,operation_type,
                 target_status,result_text,error_code)
                VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'complete','succeeded','once',NULL)""",
                ("b" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp))
            db.commit()
        self._age_reservation_operation(key)
        barrier = Barrier(2)

        def reconcile():
            barrier.wait(timeout=5)
            return self.jobs.reconcile_reservation_operations(stale_after_seconds=1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: reconcile(), range(2)))

        self.assertEqual(sum(item["completed"] for item in results), 1)
        self.assertEqual(sum(item["abandoned"] for item in results), 0)
        self.assertEqual(sum(item["deferred"] for item in results), 0)
        recovered = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(recovered.result_text, "once")
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "released",
        )

    def test_reconciler_releases_active_reservation_for_pending_completion(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "system-complete-" + claimed.job_id
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO inference_reservation_operations
                (operation_id,owner_id,job_id,device_id,idempotency_key,
                 memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                 reservation_id,fencing_token,created_at,updated_at,operation_type,
                 target_status,result_text,error_code)
                VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'complete','failed',NULL,'worker_failed')""",
                ("f" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp))
            db.commit()
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        recovered = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(recovered.status, "failed")
        self.assertEqual(recovered.error_code, "worker_failed")
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "released",
        )

    def test_reconciler_finishes_completion_after_ledger_release_crash_window(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "system-complete-" + claimed.job_id
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO inference_reservation_operations
                (operation_id,owner_id,job_id,device_id,idempotency_key,
                 memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                 reservation_id,fencing_token,created_at,updated_at,operation_type,
                 target_status,result_text,error_code)
                VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'complete','succeeded','recovered',NULL)""",
                ("e" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp))
            db.commit()
        self.reservations.release(
            self.device.token, self.reservation.reservation_id,
            self.reservation.token, fencing_token=self.reservation.fencing_token,
        )
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        recovered = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(recovered.result_text, "recovered")
        self.assertIsNone(recovered.lease_expires_at)
        self.assertIsNone(recovered.lease_token)


    def test_completion_recovery_rejects_mismatched_reservation_fence(self):
        claimed = self.jobs.claim_next(self.device.token)
        key = "system-complete-" + claimed.job_id
        stamp = "2000-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO inference_reservation_operations
                (operation_id,owner_id,job_id,device_id,idempotency_key,
                 memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                 reservation_id,fencing_token,created_at,updated_at,operation_type,
                 target_status,result_text,error_code)
                VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'complete','succeeded','must-not-commit',NULL)""",
                ("d" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,stamp,stamp))
            db.commit()
        with closing(sqlite3.connect(self.reservations.path)) as db:
            db.execute(
                "UPDATE resource_reservations SET task_id=? WHERE reservation_id=?",
                ("f" * 32,self.reservation.reservation_id),
            )
            db.commit()
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 0, "abandoned": 1, "deferred": 0},
        )
        job = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_code, "reservation_invalid")
        self.assertIsNone(job.lease_token)
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id,self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")


    def test_cancellation_recovery_rejects_mismatched_reservation_fence(self):
        key_prefix = "cancel-" + self.job.job_id + "-"
        with closing(sqlite3.connect(self.reservations.path)) as db:
            db.execute(
                "UPDATE resource_reservations SET task_id=? WHERE reservation_id=?",
                ("f" * 32,self.reservation.reservation_id),
            )
            db.commit()
        with self.assertRaises(StaleReservation):
            self.jobs.cancel_job(self.owner_id,self.job.job_id)
        with closing(sqlite3.connect(self.database)) as db:
            key = db.execute(
                "SELECT idempotency_key FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND operation_type='cancel' AND state='pending'",
                (self.owner_id,self.job.job_id),
            ).fetchone()[0]
        self.assertTrue(key.startswith(key_prefix))
        self._age_reservation_operation(key)
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 0, "abandoned": 1, "deferred": 0},
        )
        job = self.jobs.get_job(self.owner_id,self.job.job_id)
        self.assertEqual(job.status,"failed")
        self.assertEqual(job.error_code,"reservation_invalid")
        self.assertIsNone(job.lease_token)
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id,self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"],"active")

    def test_wrong_lease_token_cannot_complete_job(self):
        claimed = self.jobs.claim_next(self.device.token)
        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token="z" * 43, result_text="forged",
            )
        self.assertEqual(self.jobs.get_job(self.owner_id, self.job.job_id).status, "running")



    def test_device_revocation_supersedes_interrupted_completion_intent(self):
        claimed = self.jobs.claim_next(self.device.token)
        stamp = "2000-01-01T00:00:00+00:00"
        key = "system-complete-" + claimed.job_id
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                """INSERT INTO inference_reservation_operations
                   (operation_id,owner_id,job_id,device_id,idempotency_key,
                    memory_bytes,cpu_millicores,gpu_memory_bytes,state,
                    reservation_id,fencing_token,created_at,updated_at,
                    operation_type,target_status,result_text,error_code,lease_token_hash)
                   VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'complete','succeeded',?,NULL,?)""",
                ("b" * 32,self.owner_id,claimed.job_id,claimed.device_id,key,
                 self.reservation.reservation_id,self.reservation.fencing_token,
                 stamp,stamp,"must-not-survive-revocation",
                 __import__("hashlib").sha256(claimed.lease_token.encode("ascii")).hexdigest()),
            )
            db.commit()

        self.assertTrue(self.agent.revoke_device(self.owner_id, claimed.device_id))

        job = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual((job.status, job.error_code), ("failed", "device_revoked"))
        self.assertIsNone(job.lease_token)
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "released",
        )
        with closing(sqlite3.connect(self.database)) as db:
            operation = db.execute(
                """SELECT state FROM inference_reservation_operations
                   WHERE owner_id=? AND idempotency_key=?""",
                (self.owner_id,key),
            ).fetchone()
        self.assertEqual(operation[0], "abandoned")
        self.assertEqual(self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
                         {"completed": 0, "abandoned": 0, "deferred": 0})
        self.assertEqual(self.jobs.get_job(self.owner_id, claimed.job_id).status, "failed")

    def test_device_revocation_terminally_invalidates_jobs_and_releases_capacity(self):
        claimed = self.jobs.claim_next(self.device.token)
        queued = self._new_unreserved_job("queued before revoke")
        device_id = self.device.device.device_id

        revoked = self.agent.revoke_device(self.owner_id, device_id)

        self.assertTrue(revoked)
        running = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(running.status, "failed")
        self.assertEqual(running.error_code, "device_revoked")
        self.assertIsNone(running.lease_expires_at)
        self.assertIsNone(running.lease_token)
        queued_after = self.jobs.get_job(self.owner_id, queued.job_id)
        self.assertEqual(queued_after.status, "failed")
        self.assertEqual(queued_after.error_code, "device_revoked")
        ledger = self.reservations.get_reservation_by_id(
            self.owner_id, self.reservation.reservation_id,
        )
        self.assertEqual(ledger["status"], "released")
        with self.assertRaisesRegex(PermissionError, "invalid_device_credential"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )


    def test_pending_device_revocation_is_recovered_after_ledger_failure(self):
        claimed = self.jobs.claim_next(self.device.token)
        device_id = self.device.device.device_id
        self.pairing.begin_device_revocation(self.owner_id, device_id)

        def fail_ledger():
            raise RuntimeError("simulated ledger interruption")

        with self.assertRaisesRegex(RuntimeError, "simulated ledger interruption"):
            self.pairing.revoke_device(
                self.owner_id,
                device_id,
                transaction_callback=lambda db: self.jobs.revoke_device_jobs_in_transaction(
                    db, self.owner_id, device_id,
                ),
                before_commit=fail_ledger,
            )

        self.assertEqual(
            self.pairing.pending_device_revocations(),
            [(self.owner_id, device_id)],
        )
        recovered = self.agent.reconcile_device_revocations()
        self.assertEqual(recovered, {"completed": 1, "deferred": 0})
        self.assertEqual(self.pairing.pending_device_revocations(), [])
        terminal = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(terminal.status, "failed")
        self.assertEqual(terminal.error_code, "device_revoked")
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_id, self.reservation.reservation_id,
            )["status"],
            "released",
        )

    def test_pending_revocation_reconciles_after_pairing_commit_before_ack(self):
        claimed = self.jobs.claim_next(self.device.token)
        device_id = self.device.device.device_id
        self.pairing.begin_device_revocation(self.owner_id, device_id)
        self.pairing.revoke_device(
            self.owner_id,
            device_id,
            transaction_callback=lambda db: self.jobs.revoke_device_jobs_in_transaction(
                db, self.owner_id, device_id,
            ),
            before_commit=lambda: self.reservations.revoke_device_reservations(
                self.owner_id, device_id,
            ),
        )
        # Simulate process death before complete_device_revocation().
        self.assertEqual(len(self.pairing.pending_device_revocations()), 1)
        recovered = self.agent.reconcile_device_revocations()
        self.assertEqual(recovered, {"completed": 1, "deferred": 0})
        self.assertEqual(self.pairing.pending_device_revocations(), [])
        self.assertEqual(
            self.jobs.get_job(self.owner_id, claimed.job_id).error_code,
            "device_revoked",
        )

    def test_device_revocation_rolls_back_job_invalidation_if_ledger_step_fails(self):
        claimed = self.jobs.claim_next(self.device.token)

        def fail_ledger():
            raise RuntimeError("ledger unavailable")

        with self.assertRaisesRegex(RuntimeError, "ledger unavailable"):
            self.pairing.revoke_device(
                self.owner_id,
                self.device.device.device_id,
                transaction_callback=lambda db: self.jobs.revoke_device_jobs_in_transaction(
                    db, self.owner_id, self.device.device.device_id,
                ),
                before_commit=fail_ledger,
            )

        self.assertIsNotNone(self.pairing.authenticate_device(self.device.token))
        still_running = self.jobs.get_job(self.owner_id, claimed.job_id)
        self.assertEqual(still_running.status, "running")
        renewed = self.jobs.renew_job_lease(
            self.device.token, claimed.job_id, lease_token=claimed.lease_token,
        )
        self.assertEqual(renewed.status, "running")
        self.assertEqual(renewed.lease_token, claimed.lease_token)


if __name__ == "__main__":
    unittest.main()
