# Approved agent knowledge promotion

`ApprovedKnowledgePromoter` is the explicit boundary between reviewed agent work and durable `AgentMemoryStore` content.

## Required approval sequence

1. The owner approves the evaluation report in `AgentEvaluationReviewQueue`.
2. The owner separately calls `review_candidate` with the exact proposed content, source, agent, category, scope, confidence, and retention period.
3. The promoter hashes all those fields. Any later change to the candidate invalidates the approval.
4. `promote` verifies both approvals and atomically inserts the memory plus its promotion ledger entry.

The review queue and memory store must share the same SQLite database.

```python
from core.agent_memory import AgentMemoryStore
from evaluation.agent_review_queue import AgentEvaluationReviewQueue
from evaluation.approved_knowledge_promoter import ApprovedKnowledgePromoter

memory = AgentMemoryStore(database_path, authorize=authorize)
reviews = AgentEvaluationReviewQueue(database_path, authorize=authorize)
promoter = ApprovedKnowledgePromoter(memory, reviews)

# First approve the evaluation report through the review queue.
reviews.decide(reviewer_actor_id=owner_id, evaluation_id=evaluation_id,
               decision="approved")

# Then explicitly approve this exact candidate and its provenance.
candidate = {
    "actor_id": owner_id, "evaluation_id": evaluation_id,
    "agent_id": agent_id, "content": exact_candidate_text,
    "source": checked_evidence_reference, "category": "semantic",
    "scope": "private", "confidence": 0.9, "retention_days": 365,
}
promoter.review_candidate(**candidate, decision="approved")

# Promotion accepts only the exact same candidate parameters.
result = promoter.promote(**candidate)
```

## Trust and transaction guarantees

- Evaluation approval and exact candidate approval are separate owner actions.
- Candidate approval binds content, provenance, agent, category, scope, confidence, and retention with SHA-256.
- A content or metadata substitution after review is rejected.
- Private candidates require `agent_memory:write`; shared candidates separately require `agent_memory:share`.
- Evaluation verification, candidate approval verification, duplicate check, memory insert and promotion ledger insert commit atomically in SQLite.
- A unique actor/evaluation/candidate key makes retries idempotent.
- Existing exact memory content is recorded as a duplicate rather than copied.
- Only specifically approved knowledge is marked verified and retains evaluation provenance.
- Promotion never changes routing, agent instructions, or model weights.

## Current limitations and follow-up

- Duplicate detection is deterministic exact-text matching, not semantic contradiction detection. Conflicting claims must remain separate candidates until an evidence-aware conflict review exists.
- The promotion ledger preserves the original decision. Memory deletion removes the memory and vector cache; a follow-up revocation hook should mark the ledger revoked and retain an audit event.
- The promoter uses the current SQLite schema directly and is intended for same-database local deployments. Multi-process production deployments need migration management, backup/restore tests, and database concurrency review.
- Do not expose candidate approval or promotion as general-purpose specialist-agent tools. They must be reachable only through the authenticated owner workflow.
