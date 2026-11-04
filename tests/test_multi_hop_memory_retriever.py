import unittest

from core.multi_hop_memory_retriever import MultiHopMemoryRetriever


class FakeKnowledgeAPI:
    def __init__(self, responses=None, fail_on_call=None):
        self.responses = responses or {}
        self.fail_on_call = fail_on_call
        self.calls = []

    def retrieve(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("simulated_hop_failure")
        return {
            "actor_id": kwargs["actor_id"],
            "agent_id": kwargs["agent_id"],
            "evidence": list(self.responses.get(kwargs["query"], [])),
        }


def evidence(memory_id, content, *, verified=True):
    return {
        "memory_id": memory_id,
        "agent_id": "research",
        "category": "semantic",
        "content": content,
        "source": "test",
        "confidence": 0.9,
        "verified": verified,
        "lifecycle_status": "active",
        "created_at": 1,
        "expires_at": None,
        "superseded_by": None,
        "score": 0.9,
        "evidence": {},
        "trust": "untrusted_reference_data",
    }


class MultiHopMemoryRetrieverTests(unittest.TestCase):
    def test_expands_through_evidence_and_preserves_query_lineage(self):
        first = evidence("a" * 32, "Kubernetes canary rollout strategy")
        second = evidence("b" * 32, "Canary rollout monitors service latency thresholds")
        api = FakeKnowledgeAPI({
            "deployment": [first],
            "kubernetes canary rollout": [second],
        })
        result = MultiHopMemoryRetriever(api, max_hops=1).retrieve(
            actor_id="owner", agent_id="research", query="deployment",
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["query_count"], 2)
        self.assertEqual(result["queries"][1]["source_memory_id"], first["memory_id"])
        self.assertEqual(result["edges"][0]["source_memory_id"], first["memory_id"])
        self.assertEqual(result["edges"][0]["target_memory_id"], second["memory_id"])
        self.assertTrue(result["complete"])
        self.assertTrue(result["policy"]["authorization_checked_on_every_hop"])

    def test_every_hop_keeps_original_owner_agent_and_permissions(self):
        first = evidence("a" * 32, "Kubernetes canary rollout strategy")
        second = evidence("b" * 32, "Canary rollout monitors latency")
        api = FakeKnowledgeAPI({
            "deployment": [first],
            "kubernetes canary rollout": [second],
        })
        MultiHopMemoryRetriever(api, max_hops=1).retrieve(
            actor_id="owner", agent_id="research", query="deployment",
            include_shared=False, verified_only=True,
        )
        self.assertEqual(len(api.calls), 2)
        for call in api.calls:
            self.assertEqual(call["actor_id"], "owner")
            self.assertEqual(call["agent_id"], "research")
            self.assertFalse(call["include_shared"])
            self.assertTrue(call["verified_only"])

    def test_duplicate_records_are_returned_once(self):
        first = evidence("a" * 32, "Kubernetes canary rollout strategy")
        api = FakeKnowledgeAPI({
            "deployment": [first],
            "kubernetes canary rollout": [first],
        })
        result = MultiHopMemoryRetriever(api, max_hops=1).retrieve(
            actor_id="owner", agent_id="research", query="deployment",
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["evidence"]), 1)

    def test_second_hop_failure_propagates_instead_of_returning_complete_partial_result(self):
        first = evidence("a" * 32, "Kubernetes canary rollout strategy")
        api = FakeKnowledgeAPI({"deployment": [first]}, fail_on_call=2)
        with self.assertRaisesRegex(RuntimeError, "simulated_hop_failure"):
            MultiHopMemoryRetriever(api).retrieve(
                actor_id="owner", agent_id="research", query="deployment",
            )

    def test_query_and_record_budgets_are_enforced(self):
        first = evidence("a" * 32, "Kubernetes canary rollout strategy")
        api = FakeKnowledgeAPI({"deployment": [first]})
        result = MultiHopMemoryRetriever(
            api, max_hops=1, max_queries=1, max_records=1,
        ).retrieve(actor_id="owner", agent_id="research", query="deployment")
        self.assertEqual(result["query_count"], 1)
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["complete"])

    def test_invalid_limits_and_scope_are_rejected(self):
        api = FakeKnowledgeAPI()
        with self.assertRaises(ValueError):
            MultiHopMemoryRetriever(api, max_hops=4)
        with self.assertRaises(ValueError):
            MultiHopMemoryRetriever(api, max_queries=0)
        with self.assertRaises(ValueError):
            MultiHopMemoryRetriever(api, max_records=True)
        retriever = MultiHopMemoryRetriever(api)
        with self.assertRaises(ValueError):
            retriever.retrieve(actor_id="owner", agent_id="research", query="x" * 1001)
        with self.assertRaises(ValueError):
            retriever.retrieve(actor_id="owner", agent_id="research", query="deployment", include_shared=1)


if __name__ == "__main__":
    unittest.main()
