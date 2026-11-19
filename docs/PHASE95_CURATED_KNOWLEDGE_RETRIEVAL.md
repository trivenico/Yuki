# Phase 95 — Live-authorized curated knowledge retrieval

## Scope

Curated knowledge is now available as bounded, relevance-ranked reference context for conversation execution. It remains separate from system guidance and tool authorization.

## Retrieval path

1. The conversation runtime receives an application-supplied `knowledge_entries_for_owner(owner_id, query)` provider.
2. `CuratedKnowledgeProvider` validates the owner and query.
3. It resolves the current actor through the supplied identity callback.
4. It calls `ObservationKnowledgeReviewService.list_curated` on every request. That service enforces `memory:knowledge:read` and catalog owner matching.
5. The provider ranks active records using deterministic token overlap and returns at most five entries from at most 100 candidates.
6. `ConversationService` serializes those records with provenance as explicitly untrusted reference data.
7. `AgentExecutionLoop` adds the reference as a separate user-role message; system guidance and guarded tool policy are unchanged.

## Security and freshness

- No retrieval cache is used. Permission revocation and deactivation are visible on the next request.
- Memory Fabric is deliberately not used for authorization decisions or this retrieval path. A future cache must authenticate before lookup and support catalog-version/deactivation invalidation.
- Owner mismatch and missing actor identity fail closed.
- Retrieved values and summaries are evidence only, never instructions, permissions, consent or policy.
- Relevance ranking is deterministic lexical matching, not semantic/vector search.
- The provider is an integration component; deployments must compose it with a live actor/session callback and pass it as `knowledge_entries_for_owner` to `build_conversation_application_runtime`.
- Exact ranking quality, production load and model behavior still require evaluation.

## Not included

- Embeddings or vector database.
- Memory Fabric caching.
- Cross-owner or global knowledge.
- Autonomous promotion, prompt changes, policy changes or model training.
- Automatic inference of application/browser context from raw page contents.

## Desktop integration follow-up

The desktop Learning tab now exposes an explicit **Promote approved** action. It requires confirmation and calls the authorized review service, which independently enforces `memory:knowledge:promote`. The curated catalog is stored separately from review proposals under the private desktop data root.

The desktop conversation composition now passes the live-session-backed `CuratedKnowledgeProvider` through the shared desktop composition seam into the canonical conversation runtime. Each conversation request therefore performs owner-scoped, live-authorized retrieval before model execution. Sign-out clears desktop references and revokes the session; the provider itself does not cache records.

The UI button is enabled for approved proposals, but the service remains the authorization boundary: accounts without the promotion grant receive a permission failure and cannot promote.
