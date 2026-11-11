import os
import tempfile
import unittest
from pathlib import Path

from knowledge.evidence_ledger import EvidenceLedger


class EvidenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "evidence.sqlite"
        self.ledger = EvidenceLedger(self.path, owner_id="owner-one")

    def tearDown(self):
        self.tmp.cleanup()

    def test_claims_are_proposals_and_persist_with_evidence(self):
        claim = self.ledger.create_claim("The owner prefers concise answers.")
        self.assertEqual(claim["status"], "proposed")
        self.ledger.add_evidence(
            claim["id"], source_id="conversation:1", chunk_id="chunk:1",
            span_start=0, span_end=20,
        )
        reopened = EvidenceLedger(self.path, owner_id="owner-one").get_claim(claim["id"])
        self.assertEqual(reopened["evidence"][0]["freshness"], "unknown")
        self.assertEqual(reopened["evidence"][0]["source_id"], "conversation:1")

    def test_verification_requires_two_sources_and_current_chunks(self):
        claim = self.ledger.create_claim("A supported claim.")
        self.ledger.add_evidence(claim["id"], source_id="source-a", chunk_id="a1")
        with self.assertRaisesRegex(ValueError, "two distinct sources"):
            self.ledger.review_claim(
                claim["id"], reviewer_id="owner", decision="verified",
                source_is_current=lambda source, chunk: True,
            )
        self.ledger.add_evidence(claim["id"], source_id="source-b", chunk_id="b1")
        with self.assertRaisesRegex(ValueError, "current-source resolver"):
            self.ledger.review_claim(claim["id"], reviewer_id="owner", decision="verified")
        with self.assertRaisesRegex(ValueError, "current source chunks"):
            self.ledger.review_claim(
                claim["id"], reviewer_id="owner", decision="verified",
                source_is_current=lambda source, chunk: source != "source-b",
            )
        verified = self.ledger.review_claim(
            claim["id"], reviewer_id="owner", decision="verified",
            source_is_current=lambda source, chunk: True,
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual({e["freshness"] for e in verified["evidence"]}, {"current"})

    def test_stale_evidence_is_visible_and_blocks_verification(self):
        claim = self.ledger.create_claim("A claim with stale evidence.")
        self.ledger.add_evidence(claim["id"], source_id="source-a", chunk_id="a1")
        self.ledger.add_evidence(claim["id"], source_id="source-b", chunk_id="b1")
        resolver = lambda source, chunk: source != "source-b"
        result = self.ledger.get_claim(claim["id"], source_is_current=resolver)
        self.assertEqual(
            {item["freshness"] for item in result["evidence"]}, {"current", "stale"}
        )
        with self.assertRaisesRegex(ValueError, "current source chunks"):
            self.ledger.review_claim(
                claim["id"], reviewer_id="owner", decision="verified",
                source_is_current=resolver,
            )

    def test_refuting_evidence_blocks_verification(self):
        claim = self.ledger.create_claim("A disputed claim.")
        self.ledger.add_evidence(claim["id"], source_id="source-a", chunk_id="a1")
        self.ledger.add_evidence(claim["id"], source_id="source-b", chunk_id="b1")
        self.ledger.add_evidence(
            claim["id"], source_id="source-c", chunk_id="c1", stance="refutes"
        )
        with self.assertRaisesRegex(ValueError, "refuting evidence"):
            self.ledger.review_claim(
                claim["id"], reviewer_id="owner", decision="verified",
                source_is_current=lambda source, chunk: True,
            )

    def test_review_reopen_and_audit_history(self):
        claim = self.ledger.create_claim("Reviewable.")
        rejected = self.ledger.review_claim(
            claim["id"], reviewer_id="owner", decision="rejected", rationale="Insufficient support."
        )
        self.assertEqual(rejected["status"], "rejected")
        with self.assertRaisesRegex(ValueError, "must be proposed"):
            self.ledger.add_evidence(claim["id"], source_id="s1", chunk_id="c1")
        reopened = self.ledger.reopen_claim(claim["id"], reviewer_id="owner", rationale="New evidence.")
        self.assertEqual(reopened["status"], "proposed")
        self.assertEqual([event["decision"] for event in reopened["reviews"]], ["rejected", "reopened"])

    def test_owner_isolation_and_forget_cascade(self):
        claim = self.ledger.create_claim("Private claim.")
        self.ledger.add_evidence(claim["id"], source_id="s1", chunk_id="c1")
        other = EvidenceLedger(self.path, owner_id="owner-two")
        with self.assertRaises(KeyError):
            other.get_claim(claim["id"])
        self.assertFalse(other.forget_claim(claim["id"]))
        self.assertTrue(self.ledger.forget_claim(claim["id"]))
        with self.assertRaises(KeyError):
            self.ledger.get_claim(claim["id"])

    def test_invalid_spans_and_evidence_bounds_are_rejected(self):
        claim = self.ledger.create_claim("Bounded.")
        with self.assertRaises(ValueError):
            self.ledger.add_evidence(
                claim["id"], source_id="s1", chunk_id="c1", span_start=4
            )
        with self.assertRaises(ValueError):
            self.ledger.add_evidence(
                claim["id"], source_id="s1", chunk_id="c1", span_start=5, span_end=4
            )

    def test_database_permissions_are_private_on_posix(self):
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
