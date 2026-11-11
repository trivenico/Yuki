# External architecture review: GBrain

**Reviewed:** 2026-11-11  
**Source:** https://github.com/garrytan/gbrain  
**License stated upstream:** MIT. Verify current license and notices before copying implementation.

## Executive summary

GBrain is an agent-oriented knowledge and memory system, not a general-purpose foundation model. Its strongest architectural lesson for Yuki is that persistent memory should become an evidence-backed knowledge service with retrieval, synthesis, typed relationships, provenance, freshness checks, contradiction detection, and a durable background maintenance loop.

This review is based on the public repository README and linked architecture descriptions visible during review. Performance numbers and scale claims are upstream claims, not independently reproduced by Yuki.

## What GBrain demonstrates

1. Retrieval and synthesis are separate operations. Fast hybrid search returns evidence; a deeper think path composes a cited answer and explicitly reports missing, stale, uncited, or conflicting evidence.
2. Hybrid retrieval is layered: vector search, keyword/BM25, reciprocal-rank fusion, source-tier boosts, query rewriting and reranking, with diagnostics explaining ranking contributions.
3. A typed knowledge graph complements embeddings. Page writes extract entity references and typed edges; multi-hop graph traversal answers relational questions that semantic similarity alone may miss.
4. Memory capture is a first-class workflow. Text, files, stdin, inbox folders and integration adapters feed a durable capture pipeline with receipts.
5. Knowledge schemas evolve deliberately. Schema packs define entity types, links and extraction behavior; detection/suggestion is separated from human review and activation.
6. Background maintenance is a product subsystem: scheduled enrichment, deduplication, citation repair, salience scoring, contradiction detection and preparation tasks run as durable jobs.
7. Agent work survives crashes. GBrain describes durable subagent jobs with two-phase persistence, child-job timeouts and outbound rate leases.
8. Evaluation is continuous: long-memory benchmarks, query replay, named-entity retrieval gates, cross-harness conformance and contradiction evaluation.
9. Operational diagnostics matter. Health checks, search explain/diagnose, retry health, repair and recovery commands make the brain inspectable instead of opaque.
10. A small stable memory protocol can be better than exposing a huge tool surface. GBrain recommends five core verbs: recall, remember, entity, synthesize, forget.

## What Yuki should implement

### P0 — evidence-backed memory contracts

- Introduce explicit MemoryRecord, SourceArtifact, Entity, Relation, EvidenceSpan, MemoryRevision, and FreshnessState contracts.
- Preserve source, capture time, observed time, author/owner, consent scope, confidence, sensitivity, retention, and deletion provenance.
- Distinguish direct owner statements, imported source facts, model inferences, and unresolved hypotheses.
- Make forget a real deletion workflow across indexes, graph edges, derived summaries, caches and backups under a documented retention policy.
- Never silently convert model-generated guesses into owner facts.

### P1 — retrieval and synthesis

- Keep raw retrieval (recall/search) separate from evidence-grounded synthesis (synthesize).
- Add hybrid ranking only where evaluations show improvement: lexical + existing vector retrieval + reciprocal-rank fusion, then optional reranking.
- Every answer claim should carry source IDs and evidence spans; unsupported claims must be labeled unknown or inferred.
- Provide freshness labels and explicit gap/contradiction sections.
- Provide explainable ranking traces and diagnostics for missed evidence.

### P2 — typed graph

- Add explicit entity resolution with stable IDs and aliases.
- Begin with conservative deterministic links from explicit references and validated metadata; model extraction may propose edges but cannot commit high-impact relations without confidence thresholds or review.
- Typed, temporal, provenance-bearing edges should support multi-hop queries.
- Add duplicate candidate review and graph repair/rebuild jobs.
- Graph indexes must be rebuildable from canonical records and source artifacts.

### P3 — durable background brain worker

- Build on Yuki's existing durable task, approval, lease and worker-fencing foundations.
- Jobs: capture normalization, entity resolution, dedup suggestions, citation validation, freshness checks, contradiction candidates, salience scoring, daily digest and index maintenance.
- Each job needs idempotency keys, bounded retries, lease fencing, cancellation, checkpointing, audit events and resource budgets.
- Background jobs must never send messages, publish, spend money, or alter external accounts without the existing owner approval path.

### P4 — portable memory API and adapters

- Provide a small versioned core API for recall, remember, entity, synthesize, and forget.
- Adapt it to CLI, companion, desktop and MCP rather than building separate memory implementations per surface.
- Add import/export adapters for Markdown and a local-first vault. External integrations start read-only and require explicit source consent.
- Keep provider credentials outside prompts, skill text, logs and memory payloads.

## What Yuki already has that should be reused

- Persistent owner-scoped memory and hybrid retrieval work from earlier memory PRs.
- Memory evaluation, local embeddings, persistent indexes and backfill work.
- Durable task orchestration, worker leases, approval holds, encrypted credential broker and audit foundations.
- Transactional companion state and notification outbox.
- Authenticated API composition and installable CLI.

Do not create a parallel GBrain clone or a second unrelated job queue. Extend existing memory and task subsystems behind stable interfaces, then prove improvements with Yuki's own benchmark suite.

## Important differences and risks

- GBrain's README describes a large MCP tool surface; Yuki should start with a narrow least-privilege memory protocol and expand only when a real workflow requires it.
- A graph edge is not automatically true because it was extracted. Preserve provenance, time, confidence and correction history.
- Brain-wide visibility is risky for private data. Default to owner-private, source-specific consent; sharing must be explicit and revocable.
- A dream cycle is not permission for autonomous external actions. Keep background enrichment internal and read-only by default.
- A Markdown/git system of record is attractive for portability, but Yuki must preserve transactional guarantees for sensitive records and avoid leaking secrets into repositories.
- GBrain reports benchmark gains and large deployment scale in its README; Yuki must independently reproduce comparable workloads and must not treat vendor-reported numbers as acceptance evidence.

## Recommended implementation sequence

1. Define provenance-aware memory and entity contracts, including deletion semantics.
2. Add claim-level citations, freshness and gap analysis to synthesis.
3. Add graph edge proposals and deterministic explicit-link extraction.
4. Build a durable maintenance job set on the existing worker runtime.
5. Add LongMemEval-style and Yuki-specific retrieval/synthesis/forget regression suites.
6. Expose the five-verb memory contract consistently through CLI, companion and MCP.

## Source notes

- Project overview, synthesis, gap analysis and claimed benchmarks: https://github.com/garrytan/gbrain
- Retrieval, graph, durable jobs and evaluation overview: https://github.com/garrytan/gbrain
- Schema packs, ingestion and MCP surface: https://github.com/garrytan/gbrain

No GBrain dependency was added and no upstream code was copied in this research-only assessment.