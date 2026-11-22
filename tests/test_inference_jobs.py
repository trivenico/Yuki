import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 22, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class InferenceJobLeaseFencingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "yuki.sqlite"
        self.pairing = DevicePairingService(self.database)
        challenge = self.pairing.create_pairing_challenge("owner")
        self.device = self.pairing.pair_device(challenge.code, "worker")
        self.clock = FakeClock()
        self.jobs = InferenceJobService(self.database, self.pairing, clock=self.clock)
        self.job = self.jobs.create_job(
            "owner", model_id="local/model",
            device_id=self.device.device.device_id, input_text="hello",
        )

    def test_expired_job_is_terminal_and_never_reclaimed(self):
        first = self.jobs.claim_next(self.device.token)
        self.assertIsNotNone(first.lease_token)
        self.assertNotIn("lease_token", first.public_dict())
        self.assertNotIn(first.lease_token, repr(first))
        self.assertIsNone(self.jobs.get_job("owner", self.job.job_id).lease_token)

        second_worker_claim = self.jobs.claim_next(self.device.token)
        self.assertIsNone(second_worker_claim)

        with closing(sqlite3.connect(self.database)) as db:
            stored = db.execute(
                "SELECT lease_token_hash FROM inference_jobs WHERE job_id=?",
                (self.job.job_id,),
            ).fetchone()[0]
        self.assertNotEqual(stored, first.lease_token)

        self.clock.advance(121)
        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token=first.lease_token, result_text="late",
            )
        self.assertIsNone(self.jobs.claim_next(self.device.token))
        expired = self.jobs.get_job("owner", self.job.job_id)
        self.assertEqual(expired.status, "failed")
        self.assertEqual(expired.error_code, "lease_expired_outcome_unknown")
        expiry_events = self.jobs.list_job_events("owner", self.job.job_id)
        self.assertEqual(expiry_events[0]["event_type"], "lease_expired_outcome_unknown")
        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token=first.lease_token, result_text="stale",
            )

    def test_explicit_retry_creates_new_job_and_audit_lineage(self):
        claimed = self.jobs.claim_next(self.device.token)
        self.clock.advance(121)

        with self.assertRaisesRegex(PermissionError, "confirmation"):
            self.jobs.retry_job("owner", self.job.job_id)
        retry = self.jobs.retry_job(
            "owner", self.job.job_id, owner_confirmed=True,
        )
        self.assertNotEqual(retry.job_id, self.job.job_id)
        self.assertEqual(retry.retry_of_job_id, self.job.job_id)
        self.assertEqual(retry.status, "queued")
        self.assertEqual(retry.input_text, self.job.input_text)
        self.assertEqual(
            self.jobs.get_job("owner", self.job.job_id).status, "failed",
        )
        events = self.jobs.list_job_events("owner", retry.job_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "owner_confirmed_retry_created")
        self.assertEqual(events[0]["related_job_id"], self.job.job_id)
        parent_events = self.jobs.list_job_events("owner", self.job.job_id)
        retry_event = next(event for event in parent_events
                           if event["event_type"] == "owner_confirmed_retry_requested")
        expiry_event = next(event for event in parent_events
                            if event["event_type"] == "lease_expired_outcome_unknown")
        self.assertEqual(retry_event["related_job_id"], retry.job_id)
        self.assertEqual(expiry_event["related_job_id"], None)
        with self.assertRaisesRegex(ValueError, "not_owned"):
            self.jobs.complete_job(
                self.device.token, self.job.job_id,
                lease_token=claimed.lease_token, result_text="late",
            )

    def test_retry_is_owner_scoped_and_only_failed_jobs_are_retryable(self):
        with self.assertRaises(LookupError):
            self.jobs.retry_job("another-owner", self.job.job_id, owner_confirmed=True)
        with self.assertRaisesRegex(ValueError, "only_failed"):
            self.jobs.retry_job("owner", self.job.job_id, owner_confirmed=True)

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
