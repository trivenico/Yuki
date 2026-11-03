# Yuki Engineering Phase Ledger

Last reconciled: 2026-11-02
Repository: https://github.com/trivenico/Yuki

## Governance

This ledger is the serial-integrity source of truth. A phase is not complete because a PR exists, a document says "delivered", or a test claim appears in a PR body. Completion requires reviewed implementation on the default branch, acceptance tests on the exact merged commit, documented operational limits, and closure of linked blockers. Deferred or moved work must retain a source phase and a named destination. Unknown serials block advancement until recovered or explicitly reserved with rationale.

Allowed statuses: `complete`, `in_progress`, `blocked`, `deferred`, `moved`, `superseded`, `reserved`, `unknown`.

## Initial repository inventory

The following phase documents were present under `docs/` on 2026-11-02. "Documented; acceptance audit pending" is deliberately not a completion claim.

| Serial | Document(s) | Initial status / note |
|---:|---|---|
| 8 | PHASE8_UPSTREAM_INTEGRATION_AUDIT.md | Documented; acceptance audit pending |
| 46–51 | PHASE46_READINESS.md through PHASE51_CLOUD_REDIRECT_PROTECTION.md | Documented; acceptance audit pending |
| 53–60 | PHASE53_GEMINI_CLI_RUNTIME.md through PHASE60_PROVIDER_MODEL_CATALOG.md | Documented; acceptance audit pending |
| 62 | PHASE62_HUGGINGFACE_DISCOVERY.md; PHASE62_HUGGINGFACE_MODEL_DISCOVERY.md | **Duplicate serial; reconcile canonical scope** |
| 63–71 | PHASE63_MODEL_EVALUATION.md through PHASE71_PROVIDER_WORKER_INTEGRATION.md | Documented; acceptance audit pending |
| 73–93 | PHASE73_JULES_INSPIRED_WORKFLOWS.md through PHASE93_ENGINEERING_WORKFLOW.md | Documented; acceptance audit pending |
| 121 | PHASE121_GUARDED_AGENT_SKILL_ADAPTER.md | Documented; acceptance audit pending |
| 124–132 | PHASE124_STATIC_EXECUTION_AUDITOR.md through PHASE132_RESILIENCE_SELF_HEALING.md | Documented; acceptance audit pending |
| 136 | PHASE136_SELF_UPGRADE_PIPELINE.md | Documented; acceptance audit pending |
| 138 | PHASE138_EVIDENCE_BACKED_SPECIALIST_CAPABILITIES.md | Documented; acceptance audit pending |
| 141–143 | PHASE141_BOUNDED_AGENT_EXECUTION_LOOP.md through PHASE143_WORKER_LEASE_COORDINATION.md | Documented; acceptance audit pending |
| 145–153 | PHASE145_PROVIDER_AGENT_LOOP_INTEGRATION.md through PHASE153_OLLAMA_NATIVE_STREAMING.md | Documented; acceptance audit pending |
| 154 | No phase document found | **Unknown serial; must recover scope or explicitly reserve** |
| 155–161 | PHASE155_GENERATION_CANCELLATION.md through PHASE161_PROVIDER_SWITCHING.md | Documented; acceptance audit pending |
| 162 | GitHub issue #495 only | **Queued; not implemented** |

The ranges above are compact inventory notation, not a claim that every integer in a range has a separate document. The source filenames define the actual documented serials. Undocumented serial ranges (including 1–7, 9–45, 52, 61, 72, 94–120, 122–123, 133–135, 137, 139–140, 144, and 154) require historical classification; some may be represented by PRs/issues rather than phase documents.

## Known open blockers and sequencing

| Serial / track | Evidence | Status | Required disposition |
|---|---|---|---|
| Phase 9 agent organization / CI | Issue #9; draft PR #8 | `in_progress` / blocked | PR #8 remains open and draft. Its CI/approval gate must be reconciled with later specialist-agent work before it can be closed or superseded. |
| Phase 9 CI remediation overlap | PR #17 and its review comments | `blocked` | PR #17 is open, reported non-mergeable, broad (62 files), and has unresolved security/documentation review findings. Do not merge wholesale. Issue #9 comments report fixes on a PR branch, not proof of a safe merge. |
| Phase 95 auth DB permissions | Issue #269 | `in_progress` | Verify DB, parent directory, WAL/SHM, backup and pre-existing file behavior on supported OSes; test ignore rules and recovery. |
| Phase 137 rootless Podman acceptance | Issues #317 and #331 | `blocked` | Requires protected-main dispatch, dedicated disposable runner, immutable image digest, non-skipped integration tests, retained evidence and independent security review. PR CI alone is insufficient. |
| Phase 154 | No matching doc/issue/commit found in initial search | `unknown` | Recover intended scope from full commit/PR history or explicitly reserve with rationale before advancing beyond 153. |
| Phase 162 shared provider control plane | Issue #495 | `deferred` pending serial audit | Do not implement until all earlier serial gaps and blockers are classified. |

Other open product tracks that must be assigned to a phase only after scope/dependency analysis: issue #347 (desktop/onboarding), #367 (Windows/Linux packaging and release smoke tests), and #368 (Agent Skills repository install/management).

## Important known phase limitations retained from phase documents

- Phase 143: lease renewal is cooperative; no automatic task replay or exactly-once external side effects.
- Phase 145: no single deployed UI → enqueue → task-journal lease → approval-UI end-to-end flow.
- Phase 147: Gemini CLI and Hybrid worker support are documented as not yet available in the provider support matrix.
- Phase 150: lease expiry is not automatic turn resumption; crash reconciliation/checkpoints remain separate.
- Phase 153: native Anthropic Messages API streaming was still separate work at the time of that document; compare with PR #486 and update evidence only after merge/test review.
- Phase 155: provider cancellation is cooperative and cannot interrupt a blocked synchronous socket read.
- Phase 157: versioned DB migration tracking and real PostgreSQL integration/failover/restore/load/monitoring acceptance remain follow-ups.
- Phase 158: no public deployment, TLS edge, registration, secrets manager, or production provider/tool policy is supplied by the ASGI composition alone.
- Phase 159: CSRF/origin and trusted-host policy, edge login throttling, real PostgreSQL validation, browser accessibility/security tests and provider-by-provider streaming checks remain production follow-ups.
- Phase 160: provider status is read-only; dynamic switching is not implemented by that phase.
- Phase 161: centralized multi-process coordination, durable actor-attributed audit, connectivity probes and credential rotation remain follow-ups (Phase 162 proposal addresses the first two).

## Phase 1 current disposition (verified 2026-11-02)

| Item | Evidence | Status |
|---|---|---|
| Historical Phase 1 PR | #24 | `superseded` in scope by focused replacement; keep open until replacement disposition is final |
| Focused replacement | #497 | `in_progress`; ready for review, mergeable, 2 files changed |
| Exact-head CI | Commit `c2d29def97e2c1eca927bf9a1dd50319def0077b` | 15 reported workflow runs completed successfully, including Yuki quality gates and specialised tests |
| Independent PR review | PR #497 has no submitted reviews and no inline review threads | **Outstanding**; do not claim review complete or merge under this ledger's governance |
| Acceptance coverage | `tests/test_phase1_cli_safety.py` | Parser, missing-name, skill exception sanitization, success dispatch/data, config/agent/provider startup sanitization |

PR #497 is marked ready for review. Phase 1 remains `in_progress` until an independent review is recorded and the replacement is merged. No phase completion claim is made from CI alone.

## Phase 2 preliminary reconciliation and implementation (user-directed parallel start)

Static comparison of open draft PRs #27 and #30 against current `main` (2026-11-02):

| Candidate | Observed scope | Current-main overlap / risk | Preliminary disposition |
|---|---|---|---|
| #27 | Rotating local logs, JSONL approval audit, SQLite connection lifecycle cleanup, PermissionGate audit wiring | Current `core/approval_audit.py` is a newer SQLite event ledger with hashed pending IDs, hash-chain verification and migrations; current `core/pending_approvals.py` already uses explicit connection closing and bounded atomic claims. The PR introduces a separate JSONL audit path and duplicate audit surface. Its patch also contains a likely typo at the end of `consume_receipt`: `action_fingerprin` (missing final `t`), which requires exact source/test verification. | Do not merge wholesale. Most persistence cleanup is already present on main; determine whether any unique logging/audit wiring remains necessary. |
| #30 | Redact actor/session identities, add append-only triggers, WAL/FULL sync and migration for `approval_audit` | Current main uses a different `approval_audit_events` schema, hash-chain integrity and pending-ID hashing. The PR's old `approval_audit` schema and trigger migration are not drop-in compatible and could create a parallel/obsolete store. | Do not merge wholesale. Reconcile its privacy/integrity requirements against the current hash-chain implementation and its tests. |

Current-main evidence: `core/approval_audit.py` already implements allowed event types, hashed identifiers, a hash chain, explicit transaction handling and a legacy migration; `core/pending_approvals.py` already uses bounded JSON serialization, TTL/capacity controls, actor/session binding and explicit connection closing. This means Phase 2 must be judged by its acceptance requirements, not by whether the old draft PRs merge.

**Sequencing exception (2026-11-02):** the project owner explicitly directed work to proceed on Phase 2 while Phase 1 is deferred for later remediation. Phase 1 (#497) remains `in_progress`; this exception authorizes Phase 2 implementation work only. It does not waive independent review, acceptance testing, or the requirement to reconcile the Phase 1/2 integration before either phase is declared complete or merged out of order.

| Item | Evidence | Status | Disposition |
|---|---|---|---|
| Focused Phase 2 implementation | PR #498, head `22b36ceadb4c1900b4d23aac055273edef69aee8` | `in_progress` | Durable SQLite approval lifecycle, rotating logs, HMAC actor/session pseudonyms, append-only hash-chain audit, private file handling, durable coordinator on the real CLI skill path, read-only recovery queue, and explicit operator resolution. Reconciliation now reserves a receipt using `BEGIN IMMEDIATE`, persists decision/actor/session hashes, rejects competing decisions or identities, remains visible after interruption, and can resume after audit-write failure using idempotent audit events. Tests cover lifecycle, process claims, identity-bound reservation, interrupted lookup, retry, denial, and audit integrity. Exact-head CI, mainline reconciliation, administrative takeover/release design, and independent security review remain open. |
| Historical Phase 2 PR | #27 | `superseded` in approach, not yet closed | Contains a parallel JSONL audit store and older persistence changes. Retain as source evidence; do not merge wholesale. |
| Phase 2 hardening PR | #30 | `superseded` in approach, not yet closed | Targets an older audit schema; current main already hashes pending IDs/actors and verifies a hash chain. Its trigger migration is not a drop-in. |
| Remaining acceptance gaps | PR #498 body and CI | `in_progress` | Latest head `22b36ceadb4c1900b4d23aac055273edef69aee8`; 7 exact-head checks registered (1 running, 6 queued, 0 failures at inspection). Branch is diverged from main (98 ahead, 20 behind); integrate/reconcile main before merge. Independent security review outstanding. CLI actor is process-level `local-cli`, not verified human identity; web/API principal propagation remains open. Administrative takeover for abandoned `reconciling` reservations, multi-host storage validation, recovery crash/race tests, operational runbook, retention/archival policy, and deployment parent-directory ownership remain open. SQLite triggers/file modes are not administrator-proof tamper resistance.

## Earlier PR backlog evidence (verified 2026-11-02)

The first page of the open-PR search shows that several early serials are still represented by draft PRs, not merged completions:

| Phase / track | PR | Current state | Notes |
|---|---:|---|---|
| Phase 1 (historical) | #24 | Open, draft | Stale replacement candidate; original branch had CI import failures and obsolete patches. Do not merge wholesale. Focused replacement is PR #497. |
| Phase 2 | #27 and #30 | Open, draft | #27 currently reports non-mergeable; #30 is follow-up hardening |
| Phase 3 | #2 and #28 | Open, draft | Two different tracks claim Phase 3; scope reconciliation required |
| Phase 3 resilience | #2 | Open, draft | Separate bounded resilience/self-healing scope |
| Phase 4 | #3 | Open, draft | Persistent earning tracker |
| Phase 5 | #4 | Open, draft | Consent-based companion memory |
| Phase 6 | #5 | Open, draft | Earning opportunity research/coaching |
| Phase 7 | #6 | Open, draft | Companion orchestrator |
| Phase 8 | #7 | Open, draft | Capability readiness/gap audit |
| Phase 9 | #8 | Open, draft | CEO-style specialist organization; issue #9 records original CI blocker |
| Phase 10 | #10 and #25 | Open, draft | Provider/registry stabilization and safety evaluation are distinct follow-on work |

This evidence changes the sequencing: **Phase 1 / Phase 2 are the earliest explicit unmerged phase tracks**, ahead of Phase 9. The first phase number cannot be inferred from document inventory alone. Several early phase PRs have overlapping serials and the open PR search is not a complete audit of every PR or closed PR. Reconcile their diffs against current `main` before deciding whether a phase is already implemented elsewhere, superseded, or still incomplete.

## Next serial execution order

1. **Phase 1 is explicitly deferred by the owner**: keep PR #497 open and unmerged; return to its independent review, acceptance gaps, and stale PR #24 disposition before Phase 3 or any later serial is declared complete. The owner has asked to continue Phase 2 in the meantime.
2. Finish Phase 2 PR #498 against current `main`: exact-head cross-platform CI, independent security review, actor-attribution decision, storage-permission validation, and acceptance evidence. Close or supersede historical PRs #27/#30 only after the replacement is accepted; do not bulk-merge stale drafts.
3. Reconcile Phase 1 and Phase 2 integration and close their outstanding gates before starting Phase 3. Resolve duplicate Phase 3 tracks (#2 and #28), then Phase 4–8 in serial order (#3–#7), recording moved/superseded scope and acceptance evidence.
4. Reconcile Phase 9 PR #8 and issue #9 with later specialist-agent implementation; close or supersede only with evidence.
5. Continue the full historical serial audit, including undocumented ranges and Phase 154.
6. Complete explicit security/release blockers (#269, #317/#331) at their correct serial positions after confirming their original phase mapping.
7. Only then begin Phase 162 (#495), after all predecessor serials and acceptance gates are reconciled.

## Change log

- 2026-11-02: Initial ledger created from docs inventory and verified open issues/PR discussions. This is a baseline, not a completed historical audit.
- 2026-11-02: Owner-directed sequencing exception recorded: Phase 1 deferred, Phase 2 active; both must be reconciled before Phase 3.


## External platform assessment — Agent37 (2026-11-03)

- Added [docs/AGENT37_PLATFORM_ASSESSMENT.md](AGENT37_PLATFORM_ASSESSMENT.md), assessing Agent37's persistent sandboxes, agent provisioning/lifecycle API, durable workspaces, streaming, scheduling, integrations, observability and usage controls.
- Repository audit found existing Yuki foundations in Phases 69, 70, 74, 83, 84 and 142, including durable task journals, specialist coordination, finite-cycle worker supervision, a Docker command builder, and an approval-gated bounded coding-task runner.
- Key gap: Yuki does not yet have a tenant-isolated persistent agent-instance control plane with a hardened multi-tenant execution backend, workspace backup/restore, authenticated lifecycle API, or usage metering.
- Recommendation: reuse existing task/runtime and sandbox foundations; do not introduce a second task engine. Finish Phase 2 identity/approval/recovery gates first. Then reconcile earlier phase serials before assigning any new numbered phase to agent hosting.
- Security boundary: current Docker runner is not equivalent to a hardened multi-tenant sandbox or a gVisor/microVM boundary. No production parity claim is made.
- This is a research/architecture artifact only; it does not mark any phase complete or waive Phase 1/2 gates.

## Phase 2 follow-up — authorized stale recovery takeover

- Added explicit operator/admin role to PermissionGate; default role is operator. The role must be populated by trusted server-side authentication, never client-provided input.
- Added an admin-only, high-risk-confirmed takeover for stale `reconciling` approvals. A SQLite transaction replaces the old reservation with an admin-owned `uncertain` reservation, idempotent audit records the takeover, and successful completion returns the receipt to the recovery queue. Original skill execution is never retried.
- Same-admin retry after audit outage is supported. Tests cover role denial, fresh reservation denial, stale takeover, audit integrity, and audit-failure retry.
- Latest PR #498 head: `596cd7779638588b8f0139ca2aabd7020636241d`; 8 exact-head checks queued at inspection. Independent review, trusted API identity propagation, and production authorization integration remain open. No phase completion claimed.

## Phase 2 follow-up — verified authenticated identity propagation

- Added `core.identity.AuthenticatedIdentity`, deriving actor ID, role, and SHA-256 session pseudonym only after resolving the bearer token through `BuiltInAuthService`. Actor and session are resolved as one pair; invalid, expired, or revoked sessions are rejected.
- Authenticated API conversation ownership, cancellation keys, streaming and history now consistently use the verified identity actor ID. Provider selection admin checks use the resolved identity role.
- Added `PermissionGate.from_authenticated_identity`; only admin/operator roles may create an approval gate. End-user role is denied. Added six focused tests for pairing, role policy, forged objects, revoked sessions, malformed tokens and bearer non-disclosure.
- Latest PR #498 head: `59f2ea5dcb85825b69396f0102257a310f894908`; 15 exact-head checks queued at inspection, none failed. Branch 113 ahead / 1 behind main. CI and mainline reconciliation remain open. This is an integration seam: existing CLI approvals are not yet a multi-user web approval workflow. No phase completion claimed.

## Phase 2 CI correction — admin takeover audit allowlist

- Full Python 3.11 CI on the prior identity-propagation head exposed two cross-platform failures in the stale-admin-takeover tests. Root cause: the append-only audit event allowlist omitted `admin_reconciliation_takeover`, so the correctly fail-closed audit append rejected the new event after the atomic reservation.
- Added the event type to the explicit audit allowlist. Tests were not weakened. Latest fix head: `cf0099b94c214d83326ac464846d7ae570a3ef57`; 39 exact-head checks registered, 15 running and 24 queued at initial inspection. Revalidation pending.

## Phase 2 follow-up — request-scoped agent factory

- Added `YukiCoreAgent.from_authenticated_identity`, constructing a fresh agent and fresh PermissionGate from the verified authenticated identity. This prevents accidental cross-session sharing when callers follow the documented request-scoped construction pattern.
- Added regression coverage authenticating one admin twice and verifying separate agent/gate objects with distinct session pseudonyms.
- Latest PR #498 head: `cd1efdd6ee669227ae00613de7ccbab831962f45`; 9 exact-head checks queued at initial inspection. Branch 116 ahead / 3 behind main. CI pending. Web chat tool execution remains disabled; this factory is not yet wired to enable multi-user web tools. End-to-end runtime integration and independent review remain open.

## Phase 2 follow-up — request-scoped conversation execution boundary

- `ConversationService` now accepts an optional `agent_loop_factory(identity)` and validates that the verified identity actor matches the conversation owner.
- Any non-empty tool list is rejected unless a verified `AuthenticatedIdentity` and request-scoped agent-loop factory are supplied. This blocks accidental use of the shared process-wide execution loop for multi-user tool execution.
- The authenticated ASGI API passes its resolved identity into conversation generation. Factory output is type-checked as `AgentExecutionLoop`.
- Tests cover tool execution denial without verified identity/factory, owner mismatch, and identity propagation. Latest PR #498 head: `67c770b3cfdf29bc126cad2ab8cca2918d2f57c0`; 26 checks registered (4 running, 22 queued, no failures at initial inspection). Branch 119 ahead / 4 behind main. The web profile remains deny-by-default for tools; no production multi-user tool capability is claimed.

## Phase 2 CI correction — request identity validation ordering

- Ubuntu Python 3.11 surfaced two errors in the request-scoped conversation tests. A fixture used a placeholder conversation owner rather than the authenticated account ID; the second exposed that principal mismatch was detected only after generation lease acquisition.
- Moved verified-principal and tool-execution preconditions ahead of lease acquisition and corrected the fixture to use the authenticated actor ID. No tests were weakened or removed.
- Latest PR #498 head: `d90a813f7f86d9d7d1ffa7275f9b64828c48eb2c`; 5 exact-head checks queued at initial inspection. Revalidation pending.


## Knowledge intelligence workstream: evidence ledger

This workstream is tracked independently of serial phase numbering while the historical phase gaps above remain unresolved.

| Item | Evidence | Status | Disposition |
|---|---|---|---|
| Source-grounded claim ledger | `knowledge/evidence_ledger.py`; `tests/test_evidence_ledger.py`; `docs/KNOWLEDGE_ENGINE.md` | `in_progress` | Proposed claims, canonical chunk provenance, explicit review history, two-distinct-source verification gate, contradiction blocking, stale-evidence detection. Exact-head CI, security review, reviewer authentication boundary, and cross-database lifecycle reconciliation remain open. |
| Verified knowledge graph projection | `knowledge/verified_graph_sync.py`; `tests/test_verified_graph_sync.py`; `docs/KNOWLEDGE_ENGINE.md` | `in_progress` | Only explicitly reviewed claims with current multi-source evidence may be projected; graph projection plan drift is rejected and retries are idempotent. Exact-head CI, trusted caller authorization, provenance-aware graph reads, retraction/reconciliation, and cross-store atomicity remain open. |
| Bounded autonomous research planner | `research/planner.py`; `tests/test_research_planner.py`; `docs/KNOWLEDGE_ENGINE.md` | `in_progress` | Durable seven-stage research proposals and optional approval-gated TaskStore enqueue with idempotency, no application token quota and no wall-clock quota. Model-backed step execution, worker policy enforcement, authorized source discovery, checkpoints, report publication and cross-database reconciliation remain open. |
| Local research retrieval worker | `research/local_worker.py`; `tests/test_local_research_worker.py`; `docs/KNOWLEDGE_ENGINE.md` | `in_progress` | Read-only local lexical retrieval with source provenance, unanswered-question reporting, resource-bounded result sets and idempotent persisted runs. Model reasoning, TaskStore worker integration, index freshness invalidation, evidence extraction, network discovery and report generation remain open. |
