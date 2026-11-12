import dataclasses
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evaluation.model_eval import EvalCase, ModelEvaluator
from evaluation.run_store import EvaluationRunConflict, EvaluationRunStore


class EvaluationRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "evaluation.sqlite3")
        self.store = EvaluationRunStore(self.database)
        self.report = ModelEvaluator().run(
            "candidate-a", lambda prompt: "A bounded retry is safe.",
            [EvalCase("retry", "reasoning", "Explain a bounded retry.", required_terms=("retry",))],
            approval_required=True,
        )
        self.arguments = dict(
            owner_id="owner-a", idempotency_key="request-1",
            benchmark_id="core-reasoning", benchmark_version=1,
            benchmark_digest="a" * 64, candidate_id="candidate-a",
            provider_id="local", model_id="test-model",
            environment={"python_version": "3.11", "platform": "linux"},
            report=self.report,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_persists_and_verifies_immutable_report_across_reopen(self):
        run = self.store.record(**self.arguments)
        reopened = EvaluationRunStore(self.database)
        loaded = reopened.get(owner_id="owner-a", run_id=run.run_id)
        self.assertEqual(loaded, run)
        self.assertTrue(reopened.verify(loaded))
        self.assertEqual(loaded.benchmark_digest, "a" * 64)
        self.assertEqual(loaded.report_digest, run.report_digest)

    def test_idempotency_is_owner_scoped_and_payload_bound(self):
        first = self.store.record(**self.arguments)
        replay = self.store.record(**self.arguments)
        self.assertEqual(first.run_id, replay.run_id)
        other_owner = dict(self.arguments, owner_id="owner-b")
        second = self.store.record(**other_owner)
        self.assertNotEqual(first.run_id, second.run_id)
        replay_report = dataclasses.replace(
            self.report, generated_at="2030-01-01T00:00:00+00:00",
        )
        replayed_result = self.store.record(**dict(self.arguments, report=replay_report))
        self.assertEqual(first.run_id, replayed_result.run_id)
        changed = dict(self.arguments, model_id="different-model")
        with self.assertRaises(EvaluationRunConflict):
            self.store.record(**changed)

    def test_durable_claim_allows_only_one_live_owner_per_idempotency_key(self):
        from evaluation.run_store import EvaluationRunInProgress
        digest = self.store.request_digest_for(
            benchmark_id="core-reasoning", benchmark_version=1,
            benchmark_digest="a" * 64, candidate_id="candidate-a",
            provider_id="local", model_id="test-model",
            environment={"python_version": "3.11", "platform": "linux"},
        )
        token = self.store.acquire_claim(
            owner_id="owner-a", idempotency_key="claim-1",
            request_digest=digest,
        )
        self.assertTrue(token)
        with self.assertRaises(EvaluationRunInProgress):
            EvaluationRunStore(self.database).acquire_claim(
                owner_id="owner-a", idempotency_key="claim-1",
                request_digest=digest,
            )
        with self.assertRaises(EvaluationRunConflict):
            self.store.acquire_claim(
                owner_id="owner-a", idempotency_key="claim-1",
                request_digest="b" * 64,
            )
        self.assertTrue(self.store.release_claim(
            owner_id="owner-a", idempotency_key="claim-1", lease_token=token,
        ))
        replacement = self.store.acquire_claim(
            owner_id="owner-a", idempotency_key="claim-1",
            request_digest=digest,
        )
        self.assertTrue(replacement)
        self.assertNotEqual(token, replacement)

    def test_claim_token_is_required_to_commit_claimed_run(self):
        from evaluation.run_store import EvaluationRunInProgress
        digest = self.store.request_digest_for(
            benchmark_id="core-reasoning", benchmark_version=1,
            benchmark_digest="a" * 64, candidate_id="candidate-a",
            provider_id="local", model_id="test-model",
            environment={"python_version": "3.11", "platform": "linux"},
        )
        token = self.store.acquire_claim(
            owner_id="owner-a", idempotency_key="request-1",
            request_digest=digest,
        )
        with self.assertRaises(EvaluationRunInProgress):
            self.store.record(**self.arguments, lease_token="not-the-lease")
        committed = self.store.record(**self.arguments, lease_token=token)
        self.assertEqual(committed.run_id, self.store.get_by_idempotency(
            owner_id="owner-a", idempotency_key="request-1",
        ).run_id)
        self.assertFalse(self.store.release_claim(
            owner_id="owner-a", idempotency_key="request-1", lease_token=token,
        ))

    def test_reads_and_lists_never_cross_owner_boundaries(self):
        run = self.store.record(**self.arguments)
        with self.assertRaises(KeyError):
            self.store.get(owner_id="owner-b", run_id=run.run_id)
        self.store.record(**dict(self.arguments, owner_id="owner-b", idempotency_key="request-2"))
        self.assertEqual(len(self.store.list_runs(owner_id="owner-a")), 1)
        self.assertEqual(len(self.store.list_runs(owner_id="owner-b")), 1)

    def test_rejects_unapproved_environment_fields_and_invalid_digests(self):
        with self.assertRaises(ValueError):
            self.store.record(**dict(self.arguments, environment={"api_key": "secret"}))
        with self.assertRaises(ValueError):
            self.store.record(**dict(self.arguments, benchmark_digest="not-a-digest"))

    def test_report_candidate_must_match_record_candidate(self):
        with self.assertRaises(ValueError):
            self.store.record(**dict(self.arguments, candidate_id="different-candidate"))

    def test_detects_mutated_record_content(self):
        run = self.store.record(**self.arguments)
        changed = dataclasses.replace(run, model_id="tampered")
        self.assertFalse(self.store.verify(changed))

    def test_detects_database_tampering_on_read(self):
        run = self.store.record(**self.arguments)
        # sqlite3's connection context manager commits/rolls back but does not
        # close the connection. Explicitly close it so Windows can remove the
        # temporary database during tearDown.
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE evaluation_runs SET model_id='tampered' WHERE run_id=?", (run.run_id,))
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            self.store.get(owner_id="owner-a", run_id=run.run_id)

    def test_validates_bounds_and_types(self):
        with self.assertRaises(ValueError):
            self.store.record(**dict(self.arguments, benchmark_version=True))
        with self.assertRaises(ValueError):
            self.store.list_runs(owner_id="owner-a", limit=0)


if __name__ == "__main__":
    unittest.main()
