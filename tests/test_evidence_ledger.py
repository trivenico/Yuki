import tempfile
import unittest
from pathlib import Path

from knowledge.engine import KnowledgeEngine
from knowledge.evidence_ledger import EvidenceLedger


class EvidenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.engine = KnowledgeEngine(self.root / "knowledge.sqlite3")
        self.ledger = EvidenceLedger(self.engine, self.root / "evidence.sqlite3")
        self.sources = []
        self.chunk_ids = []
        for name, content in (
            ("one.md", "The Yuki project uses local semantic retrieval."),
            ("two.md", "Yuki maintains a local semantic knowledge index."),
            ("three.md", "A separate unrelated reference document."),
        ):
            path = self.workspace / name
            path.write_text(content, encoding="utf-8")
            result = self.engine.ingest_file(path, workspace=self.workspace)
            chunks = self.engine.get_source_chunks(result["source_id"])
            self.sources.append(result["source_id"])
            self.chunk_ids.append(chunks[0].chunk_id)

    def tearDown(self):
        self.temp.cleanup()

    def test_claim_is_proposed_then_supported_with_source_provenance(self):
        claim = self.ledger.create_claim(
            "Yuki uses local semantic retrieval.",
            [self.chunk_ids[0]],
            stance="supports",
        )
        self.assertEqual(claim["status"], "supported")
        self.assertEqual(len(claim["evidence"]), 1)
        self.assertEqual(claim["evidence"][0]["source_id"], self.sources[0])
        self.assertEqual(claim["reviews"], [])

    def test_verification_requires_two_distinct_sources(self):
        claim = self.ledger.create_claim(
            "Yuki uses local semantic retrieval.",
            [self.chunk_ids[0]],
        )
        with self.assertRaisesRegex(ValueError, "two distinct sources"):
            self.ledger.review_claim(
                claim["claim_id"], decision="verify", reviewer="operator",
                method="manual review", rationale="Only one source exists.",
            )
        self.ledger.add_evidence(
            claim["claim_id"], [self.chunk_ids[1]], stance="supports"
        )
        reviewed = self.ledger.review_claim(
            claim["claim_id"], decision="verify", reviewer="operator",
            method="independent source comparison",
            rationale="Two distinct source documents support the claim.",
        )
        self.assertEqual(reviewed["status"], "verified")
        self.assertEqual(len(reviewed["reviews"]), 1)
        self.assertEqual(set(reviewed["reviews"][0]["source_ids"]),
                         {self.sources[0], self.sources[1]})

    def test_refuting_only_evidence_is_not_reported_as_supported(self):
        claim = self.ledger.create_claim(
            "Yuki does not use remote-only inference.",
            [self.chunk_ids[0]],
            stance="refutes",
        )
        self.assertEqual(claim["status"], "contested")

    def test_conflicting_evidence_blocks_verification(self):
        claim = self.ledger.create_claim(
            "Yuki uses local semantic retrieval.", [self.chunk_ids[0]]
        )
        self.ledger.add_evidence(
            claim["claim_id"], [self.chunk_ids[1]], stance="refutes"
        )
        current = self.ledger.get_claim(claim["claim_id"])
        self.assertEqual(current["status"], "contested")
        with self.assertRaisesRegex(ValueError, "Contested claims"):
            self.ledger.review_claim(
                claim["claim_id"], decision="verify", reviewer="operator",
                method="manual", rationale="Conflicting evidence remains.",
            )

    def test_reviewed_claim_must_be_reopened_before_more_evidence(self):
        claim = self.ledger.create_claim(
            "Yuki uses local semantic retrieval.",
            [self.chunk_ids[0], self.chunk_ids[1]],
        )
        verified = self.ledger.review_claim(
            claim["claim_id"], decision="verify", reviewer="operator",
            method="manual", rationale="Two independent sources.",
        )
        self.assertEqual(verified["status"], "verified")
        with self.assertRaisesRegex(ValueError, "reopened"):
            self.ledger.add_evidence(
                claim["claim_id"], [self.chunk_ids[2]], stance="refutes"
            )
        reopened = self.ledger.review_claim(
            claim["claim_id"], decision="reopen", reviewer="operator",
            method="new evidence", rationale="A new source needs review.",
        )
        self.assertEqual(reopened["status"], "proposed")
        updated = self.ledger.add_evidence(
            claim["claim_id"], [self.chunk_ids[2]], stance="refutes"
        )
        self.assertEqual(updated["claim"]["status"], "contested")

    def test_rejects_stale_or_untrusted_chunk_ids(self):
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.ledger.create_claim("Unsupported claim.", [999999])
        with self.assertRaises(ValueError):
            self.ledger.create_claim("Unsupported claim.", [True])
        with self.assertRaises(ValueError):
            self.ledger.create_claim("Unsupported claim.", [])

    def test_stale_evidence_is_flagged_and_cannot_verify(self):
        claim = self.ledger.create_claim(
            "Yuki uses local semantic retrieval.",
            [self.chunk_ids[0], self.chunk_ids[1]],
        )
        source = self.workspace / "one.md"
        source.write_text("A replacement document with different content.", encoding="utf-8")
        self.engine.ingest_file(source, workspace=self.workspace)
        loaded = self.ledger.get_claim(claim["claim_id"])
        stale = [item for item in loaded["evidence"] if not item["current"]]
        self.assertEqual(len(stale), 1)
        with self.assertRaisesRegex(ValueError, "current canonical evidence"):
            self.ledger.review_claim(
                claim["claim_id"], decision="verify", reviewer="operator",
                method="manual", rationale="One cited source has been replaced.",
            )

    def test_claims_persist_across_instances(self):
        claim = self.ledger.create_claim(
            "Yuki has an independent knowledge store.", [self.chunk_ids[0]]
        )
        reopened = EvidenceLedger(self.engine, self.root / "evidence.sqlite3")
        loaded = reopened.get_claim(claim["claim_id"])
        self.assertEqual(loaded["claim_text"], claim["claim_text"])
        self.assertEqual(loaded["evidence"][0]["source_id"], self.sources[0])


if __name__ == "__main__":
    unittest.main()
