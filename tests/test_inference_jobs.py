import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService


class InferenceJobLeaseFencingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "yuki.sqlite"
        self.pairing = DevicePairingService(self.database)
        challenge = self.pairing.create_pairing_challenge("owner")
        self.device = self.pairing.pair_device(challenge.code, "worker")
        self.jobs = InferenceJobService(self.database, self.pairing)
        self.job = self.jobs.create_job(
            "owner", model_id="local/model",
            device_id=self.device.device.device_id, input_text="hello",
        )

    def _expire(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE inference_jobs SET lease_expires_at=? WHERE job_id=?",
                ("2000-01-01T00:00:00+00:00", self.job.job_id),
            )
            db.commit()

    def test_reclaim_rotates_secret_and_fences_stale_completion(self):
        first = self.jobs.claim_next(self.device.token)
        self.assertIsNotNone(first.lease_token)
        self.assertNotIn("lease_token", first.public_dict())
        self.assertNotIn(first.lease_token, repr(first))
        self.assertIsNone(self.jobs.get_job("owner", self.job.job_id).lease_token)

        with closing(sqlite3.connect(self.database)) as db:
            stored = db.execute(
                "SELECT lease_token_hash FROM inference_jobs WHERE job_id=?",
                (self.job.job_id,),
            ).fetchone()[0]
        self.assertNotEqual(stored, first.lease_token)
        self._expire()

        second = self.jobs.claim_next(self.device.token)
        self.assertNotEqual(first.lease_token, second.lease_token)
        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token=first.lease_token, result_text="stale",
            )
        self.assertEqual(self.jobs.get_job("owner", self.job.job_id).status, "running")

        completed = self.jobs.complete_job(
            self.device.token, self.job.job_id,
            lease_token=second.lease_token, result_text="current",
        )
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(self.jobs.get_job("owner", self.job.job_id).result_text, "current")

    def test_cancel_revokes_inflight_lease_token(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.assertTrue(self.jobs.cancel_job("owner", self.job.job_id))
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
