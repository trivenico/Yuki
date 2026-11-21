import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from core.device_pairing import DevicePairingService
from core.auth import BuiltInAuthService
from core.device_resource_reservations import DeviceResourceReservationService
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

        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token=claimed.lease_token, result_text="stale",
            )
        self.assertIsNone(self.jobs.claim_next(self.device.token))

    def test_live_claim_can_be_renewed(self):
        claimed = self.jobs.claim_next(self.device.token)
        original_expiry = claimed.lease_expires_at

        renewed = self.jobs.renew_job_lease(
            self.device.token, claimed.job_id, lease_token=claimed.lease_token,
        )

        self.assertEqual(renewed.status, "running")
        self.assertEqual(renewed.lease_token, claimed.lease_token)
        self.assertGreater(renewed.lease_expires_at, original_expiry)
        self.assertEqual(
            self.jobs.get_job("owner", claimed.job_id).lease_expires_at,
            renewed.lease_expires_at,
        )

    def test_expired_claim_cannot_be_renewed_or_resurrected(self):
        claimed = self.jobs.claim_next(self.device.token)
        self._expire()
        with self.assertRaisesRegex(ValueError, "lease_expired_or_not_owned"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )
        self.assertIsNone(self.jobs.claim_next(self.device.token))
        job = self.jobs.get_job("owner", claimed.job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_code, "lease_expired")

    def test_wrong_token_cannot_renew_claim(self):
        claimed = self.jobs.claim_next(self.device.token)
        with self.assertRaisesRegex(ValueError, "lease_expired_or_not_owned"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token="z" * 43,
            )
        self.assertEqual(
            self.jobs.get_job("owner", claimed.job_id).lease_expires_at,
            claimed.lease_expires_at,
        )

    def test_revoked_device_cannot_renew_claim(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.pairing.revoke_device("owner", self.device.device.device_id)
        with self.assertRaisesRegex(PermissionError, "invalid_device_credential"):
            self.jobs.renew_job_lease(
                self.device.token, claimed.job_id, lease_token=claimed.lease_token,
            )

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
        self.assertEqual(self.jobs.get_job("owner", self.job.job_id).status, "cancelled")

    def test_wrong_lease_token_cannot_complete_job(self):
        claimed = self.jobs.claim_next(self.device.token)
        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token="z" * 43, result_text="forged",
            )
        self.assertEqual(self.jobs.get_job("owner", self.job.job_id).status, "running")


if __name__ == "__main__":
    unittest.main()
