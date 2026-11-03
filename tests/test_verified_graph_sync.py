import tempfile
import unittest
from pathlib import Path

from knowledge.knowledge_graph import KnowledgeGraph
from knowledge.verified_graph_sync import VerifiedKnowledgeGraphSync


class FakeEvidenceLedger:
    def __init__(self, claim):
        self.claim = claim

    def get_claim(self, claim_id):
        if claim_id != self.claim["claim_id"]:
            raise KeyError("Unknown claim.")
        return self.claim


def make_claim(status="verified", current=True):
    evidence = [
        {"evidence_id": "evidence-1", "source_id": "source-a",
         "chunk_id": 1, "stance": "supports", "current": current,
         "content": "Yuki has a local knowledge engine."},
        {"evidence_id": "evidence-2", "source_id": "source-b",
         "chunk_id": 2, "stance": "supports", "current": current,
         "content": "Yuki maintains local knowledge."},
    ]
    return {
        "claim_id": "claim-1",
        "claim_text": "Yuki maintains a local knowledge engine.",
        "status": status,
        "evidence": evidence,
        "reviews": [{"decision": "verify", "reviewer": "operator"}],
    }


class VerifiedGraphSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.graph = KnowledgeGraph(root / "graph.json")
        self.claim = make_claim()
        self.sync = VerifiedKnowledgeGraphSync(
            FakeEvidenceLedger(self.claim), self.graph, root / "sync.sqlite3"
        )
        self.entities = [
            {"name": "Yuki", "kind": "project",
             "description": "Local AI assistant and agent platform."},
            {"name": "Knowledge Engine", "kind": "concept",
             "description": "Local source-grounded knowledge subsystem."},
        ]
        self.relations = [
            {"source": "Yuki", "type": "uses", "target": "Knowledge Engine"}
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_only_verified_claims_can_be_projected(self):
        self.claim["status"] = "supported"
        with self.assertRaisesRegex(ValueError, "Only explicitly verified"):
            self.sync.sync_claim(
                "claim-1", entities=self.entities, relations=self.relations
            )
        self.assertEqual(self.graph.list_entities(), [])

    def test_stale_evidence_blocks_projection(self):
        self.claim["evidence"][0]["current"] = False
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.sync.sync_claim(
                "claim-1", entities=self.entities, relations=self.relations
            )
        self.assertEqual(self.graph.list_entities(), [])

    def test_two_current_supporting_sources_are_required(self):
        self.claim["evidence"][1]["source_id"] = "source-a"
        with self.assertRaisesRegex(ValueError, "two current supporting sources"):
            self.sync.sync_claim(
                "claim-1", entities=self.entities, relations=self.relations
            )

    def test_projection_is_idempotent_and_persistent(self):
        first = self.sync.sync_claim(
            "claim-1", entities=self.entities, relations=self.relations
        )
        second = self.sync.sync_claim(
            "claim-1", entities=self.entities, relations=self.relations
        )
        self.assertFalse(first["already_synced"])
        self.assertTrue(second["already_synced"])
        self.assertEqual(first["entity_ids"], second["entity_ids"])
        self.assertEqual(len(self.graph.list_entities()), 2)
        self.assertEqual(len(self.graph.list_relations()), 1)
        saved = self.sync.get_sync("claim-1")
        self.assertEqual(saved["evidence_ids"], ["evidence-1", "evidence-2"])

    def test_changed_projection_requires_explicit_reconciliation(self):
        self.sync.sync_claim(
            "claim-1", entities=self.entities, relations=self.relations
        )
        changed = [*self.entities, {"name": "Local Inference", "kind": "concept"}]
        with self.assertRaisesRegex(ValueError, "projection changed"):
            self.sync.sync_claim(
                "claim-1", entities=changed, relations=self.relations
            )

    def test_invalid_relation_endpoint_is_rejected_before_graph_mutation(self):
        with self.assertRaisesRegex(ValueError, "included in the claim entity set"):
            self.sync.sync_claim(
                "claim-1", entities=self.entities,
                relations=[{"source": "Yuki", "type": "uses", "target": "Missing"}],
            )
        self.assertEqual(self.graph.list_entities(), [])

    def test_missing_explicit_verification_review_is_rejected(self):
        self.claim["reviews"] = []
        with self.assertRaisesRegex(ValueError, "no explicit verification review"):
            self.sync.sync_claim(
                "claim-1", entities=self.entities, relations=self.relations
            )


if __name__ == "__main__":
    unittest.main()
