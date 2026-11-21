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
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        ledger = self.reservations.get_reservation_by_id(self.owner_id,self.reservation.reservation_id)
        self.assertEqual(ledger["status"], "released")
        self.assertEqual(self.jobs.get_job(self.owner_id,claimed.job_id).status,"cancelled")

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
