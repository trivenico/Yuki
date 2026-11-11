# Knowledge Evidence Ledger

## Scope

The evidence ledger stores owner-scoped claim proposals, source/chunk references,
explicit review decisions, freshness checks and review history. It does not
replace the existing personal memory store, lexical memory search or knowledge
graph. It is a provenance and verification layer to be integrated behind those
systems.

## Trust and verification rules

- New claims always start as `proposed`.
- Evidence references canonical source and chunk IDs, stance (supports/refutes),
  and optional character offsets. Source text is not copied into this ledger.
- Verification requires supporting evidence from at least two distinct source
  IDs, no recorded refuting evidence, and a caller-supplied resolver confirming
  every supporting chunk is current.
- Two source IDs do not prove source independence or truth. A verified status
  means a trusted reviewer accepted the configured evidence threshold.
- Reviewer authentication and authorization are responsibilities of the
  authenticated application layer. Model-generated content must not call review
  operations directly.
- Rejected or verified claims require an explicit reviewer-driven reopen before
  evidence can change. Review history is append-only until the claim is
  deliberately forgotten.
- Forgetting a claim cascades to its evidence and review events. It does not
  delete source artifacts, vector chunks, graph nodes, summaries, caches or
  backups; integration must coordinate those systems' own deletion workflows.

## Storage and limits

The SQLite database is owner-scoped, uses transactional writes, WAL, busy
timeouts and POSIX mode 0600. The database is not encrypted at rest. Deployments
must protect the parent directory, backups and any source index separately.
Evidence and review payloads are bounded; source IDs and chunk IDs are references,
not trusted display content.

## Integration sequence

1. Connect canonical source IDs to the ingestion/indexer lifecycle.
2. Invalidate or mark evidence stale on source deletion/re-indexing.
3. Expose claim creation/evidence reads to model workflows as proposals only.
4. Expose review/reopen/forget only through authenticated owner-authorized APIs.
5. Add retrieval and synthesis evaluation before using verified claims in
   generated answers.
