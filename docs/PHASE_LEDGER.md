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

## Next serial execution order

1. Finish Phase 9 disposition: verify PR #8 scope against the current default branch and later specialist-agent implementation; resolve issue #9 based on evidence, not stale PR claims.
2. Continue serial gap recovery and audit the earliest undocumented/moved serials, especially Phase 154.
3. Complete earlier explicit security/release blockers (#269, #317/#331) in their proper serial positions after confirming their relationship to phase history.
4. Only then begin Phase 162 (#495), and only after its predecessor serials and acceptance gates are reconciled.

## Change log

- 2026-11-02: Initial ledger created from docs inventory and verified open issues/PR discussions. This is a baseline, not a completed historical audit.
