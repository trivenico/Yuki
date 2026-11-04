# Approved agent knowledge promotion

`ApprovedKnowledgePromoter` is the explicit boundary between a human-approved evaluation and durable `AgentMemoryStore` content.

## Required wiring

The review queue and memory store must share the same SQLite database. This lets the promoter verify the review decision, insert the memory, and write the promotion ledger in one `BEGIN IMMEDIATE` transaction.

```python
from core.agent_memory import AgentMemoryStore
from evaluation.agent_review_queue import AgentEvaluationReviewQueue
from evaluation.approved_knowledge_promoter import ApprovedKnowledgePromoter

memory = AgentMemoryStore(database_path, authorize=authorize)
reviews = AgentEvaluationReviewQueue(database_path, authorize=authorize)
promoter = ApprovedKnowledgePromoter(memory, reviews)

result = promoter.promote(
    actor_id=owner_id,                 # trusted authenticated identity
    evaluation_id=approved_evaluation_id,
    agent_id=reviewed_agent_id,
    content=exact_human_approved_text,
    source="checked artifact or source reference",
    category="semantic",
    scope="private",                  # shared requires agent_memory:share
)
```

## Trust and transaction guarantees

- Promotion requires an existing review with status `approved` and the reviewer must be the same owner as the memory actor.
- The review's agent ID and `eligible_for_memory_review` flag must match.
- The caller supplies the exact candidate content. The promoter does not turn model-generated explanations into facts or infer content from a score.
- Private memory requires `agent_memory:write`; shared memory separately requires `agent_memory:share`.
- Review verification, duplicate check, memory insert and promotion ledger insert commit atomically in SQLite.
- A unique `(actor_id, evaluation_id)` ledger key makes retries idempotent.
- Exact existing memory content is recorded as a duplicate rather than copied.
- Memory is tagged verified only after owner approval and keeps the evaluation ID in its source provenance.
- Promotion never changes routing, agent instructions, or model weights.

## Current limitations and follow-up

- The caller is responsible for presenting the evidence and obtaining explicit owner approval of the specific candidate text, not merely the evaluation. The current review queue approves an evaluation report; a dedicated candidate-review record should be added before exposing promotion in a UI.
- Duplicate detection is deterministic exact-text matching, not semantic contradiction detection. Conflicting claims must remain separate candidates until a future evidence-aware conflict review is built.
- The promotion ledger preserves the original promotion decision. Memory deletion removes the memory and vector cache; a follow-up revocation hook should mark the ledger revoked and retain an audit event.
- The promoter uses the current SQLite schema directly and is intended for same-database local deployments. Multi-process production deployments need migration management, backup/restore tests, and a database concurrency review.
- Do not expose this operation to specialist agents as a general-purpose tool. It should be reachable only through the authenticated owner approval workflow.