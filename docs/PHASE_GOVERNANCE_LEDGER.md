# Yuki Phase Governance Ledger

**Status:** Draft evidence inventory — not an implementation-completion register  
**Baseline:** `main` at `cb04fa60cd854e0fe5cec52b6595a2662fc5096c`  
**Tracking:** GitHub issue [#496](https://github.com/trivenico/Yuki/issues/496)  
**Last audited:** 2026-11-03

## Purpose and rules

This file is the canonical index for phase serials. A phase heading or an issue being closed is not proof of completion. A phase may be marked `complete` only when its implementation paths, relevant tests, exact-head CI evidence, and outstanding acceptance criteria have been checked.

Allowed statuses: `complete`, `in_progress`, `blocked`, `deferred`, `moved`, `superseded`, `reserved`, and `unknown`.

Rules:

1. Never infer a missing phase's scope from neighboring phases.
2. Every serial from 1 through the highest allocated serial must have a ledger row.
3. A missing serial remains `unknown` until historical evidence establishes its scope or maintainers explicitly reserve it with rationale.
4. A moved or superseded phase must name its destination/successor and preserve unfinished acceptance criteria.
5. Completion requires code + tests + exact-head CI evidence; external runtime gates and independent reviews must be recorded separately.
6. Do not advance the phase sequence past an unresolved `unknown` serial.
7. Keep this ledger and `ROADMAP.md` synchronized when phase status changes.

## Evidence inventory

The following phase documents were present in `docs/` at the baseline commit. The titles below are derived from their filenames; this inventory does **not** certify implementation completeness.

| Serial | Evidence document | Initial ledger status | Notes |
|---:|---|---|---|
| 1–7 | No `PHASE1`–`PHASE7` documents found | unknown | Historical scope not recovered in this audit |
| 8 | `PHASE8_UPSTREAM_INTEGRATION_AUDIT.md` | unknown | Audit implementation and acceptance evidence; issue #55 is a related but separately scoped upstream reuse audit |
| 9–45 | No phase documents found | unknown | Includes issue #9 for Phase 9; other serials need historical classification |
| 46 | `PHASE46_READINESS.md` | unknown | Audit implementation/tests/CI |
| 47 | `PHASE47_LIVE_SMOKE_TEST.md` | unknown | Audit runtime evidence |
| 48 | `PHASE48_CI_QUALITY_GATES.md` | unknown | Audit current workflow evidence |
| 49 | `PHASE49_PROVIDER_RUNTIME_INTEGRATION.md` | unknown | Audit implementation/tests |
| 50 | `PHASE50_CLOUD_ENDPOINT_TRUST.md` | unknown | Audit security tests |
| 51 | `PHASE51_CLOUD_REDIRECT_PROTECTION.md` | unknown | Audit security tests |
| 52 | No phase document found | unknown | Historical scope not recovered |
| 53 | `PHASE53_GEMINI_CLI_RUNTIME.md` | unknown | Audit implementation/tests |
| 54 | `PHASE54_PROVIDER_RUNTIME_HARDENING.md` | unknown | Audit implementation/tests |
| 55 | `PHASE55_PROVIDER_SMOKE_TEST.md` | unknown | Audit runtime evidence |
| 56 | `PHASE56_GITHUB_PLUGIN.md` | unknown | Audit implementation/tests |
| 57 | `PHASE57_MCP_PLUGINS.md` | unknown | Audit implementation/tests |
| 58 | `PHASE58_GITHUB_LIFECYCLE.md` | unknown | Audit implementation/tests |
| 59 | `PHASE59_CONNECTOR_MANAGEMENT.md` | unknown | Audit implementation/tests |
| 60 | `PHASE60_PROVIDER_MODEL_CATALOG.md` | unknown | Audit implementation/tests |
| 61 | No phase document found | unknown | Historical scope not recovered |
| 62 | `PHASE62_HUGGINGFACE_DISCOVERY.md` and `PHASE62_HUGGINGFACE_MODEL_DISCOVERY.md` | in_progress | Duplicate serial; relationship documented below, file-level reconciliation still pending |
| 63 | `PHASE63_MODEL_EVALUATION.md` | unknown | Audit implementation/tests |
| 64 | `PHASE64_MODEL_RECOMMENDATIONS.md` | unknown | Audit implementation/tests |
| 65 | `PHASE65_HF_MODEL_SCOUT.md` | unknown | Audit implementation/tests |
| 66 | `PHASE66_MODEL_CHANGE_DETECTION.md` | unknown | Audit implementation/tests |
| 67 | `PHASE67_MODEL_ALERTING.md` | unknown | Audit implementation/tests |
| 68 | `PHASE68_PROVIDER_CAPABILITY_REGISTRY.md` | unknown | Audit implementation/tests |
| 69 | `PHASE69_DURABLE_AGENT_RUNTIME.md` | unknown | Audit implementation/tests |
| 70 | `PHASE70_BOUNDED_AGENT_WORKER.md` | unknown | Audit implementation/tests |
| 71 | `PHASE71_PROVIDER_WORKER_INTEGRATION.md` | unknown | Audit implementation/tests |
| 72 | No phase document found | unknown | Historical scope not recovered |
| 73 | `PHASE73_JULES_INSPIRED_WORKFLOWS.md` | unknown | Audit implementation/tests |
| 74 | `PHASE74_ISOLATED_CODING_TASK_RUNNER.md` | unknown | Audit runtime isolation evidence |
| 75 | `PHASE75_CODING_TASK_EXECUTION_EVIDENCE.md` | unknown | Audit implementation/tests |
| 76 | `PHASE76_WORKFLOW_ENGINE_SAFETY.md` | unknown | Audit implementation/tests |
| 77 | `PHASE77_DURABLE_WORKFLOW_STORE.md` | unknown | Audit persistence evidence |
| 78 | `PHASE78_ATOMIC_WORKFLOW_CLAIMS.md` | unknown | Audit concurrency evidence |
| 79 | `PHASE79_DURABLE_APPROVAL_ENFORCEMENT.md` | unknown | Audit security tests |
| 80 | `PHASE80_AUTHENTICATED_WORKFLOW_APPROVAL.md` | unknown | Audit identity tests |
| 81 | `PHASE81_BUILT_IN_AUTH.md` | unknown | Open follow-up #269 covers auth DB permissions |
| 82 | `PHASE82_CAPABILITY_CONTRACTS.md` | unknown | Audit implementation/tests |
| 83 | `PHASE83_UNIFIED_TASK_RUNTIME.md` | unknown | Audit implementation/tests |
| 84 | `PHASE84_SPECIALIST_AGENT_COORDINATOR.md` | unknown | Audit implementation/tests |
| 85 | `PHASE85_SPECIALIST_HANDLER_WORKER.md` | unknown | Audit implementation/tests |
| 86 | `PHASE86_SPECIALIST_ACTION_CONTRACT.md` | unknown | Audit implementation/tests |
| 87 | `PHASE87_SELECTED_PROVIDER_SPECIALISTS.md` | unknown | Audit implementation/tests |
| 88 | `PHASE88_AUTHORIZED_READ_TOOLS.md` | unknown | Audit authorization tests |
| 89 | `PHASE89_SUPERVISED_WORKER_RECOVERY.md` | unknown | Audit recovery tests |
| 90 | `PHASE90_AUTHORIZED_EXPLICIT_RETRIES.md` | unknown | Audit authorization tests |
| 91 | `PHASE91_SPECIALIST_READ_TOOL_INTEGRATION.md` | unknown | Audit integration tests |
| 92 | `PHASE92_APPROVED_WORKSPACE_CHANGES.md` | unknown | Audit approval and filesystem tests |
| 93 | `PHASE93_ENGINEERING_WORKFLOW.md` | unknown | Audit implementation/tests |
| 94–120 | No phase documents found | unknown | Historical scope/classification required; issue #496 notes this range |
| 121 | `PHASE121_GUARDED_AGENT_SKILL_ADAPTER.md` | unknown | Audit implementation/tests |
| 122–123 | No phase documents found | unknown | Historical scope not recovered |
| 124 | `PHASE124_STATIC_EXECUTION_AUDITOR.md` | unknown | Audit implementation/tests |
| 125 | `PHASE125_METADATA_SKILL_DISCOVERY.md` | unknown | Audit implementation/tests |
| 126 | `PHASE126_APPROVED_SKILL_WIRING.md` | unknown | Audit implementation/tests |
| 127 | `PHASE127_GUARDED_APPROVED_SKILL_RUNTIME.md` | unknown | Audit security tests |
| 128 | `PHASE128_CLI_SKILL_RUNTIME_INTEGRATION.md` | unknown | Audit integration tests |
| 129 | `PHASE129_GUARDED_TOOL_DISPATCH.md` | unknown | Audit authorization tests |
| 130 | `PHASE130_ON_DEMAND_SKILL_CATALOG.md` | unknown | Audit implementation/tests |
| 131 | `PHASE131_AGENT_SKILL_CATALOG_INTEGRATION.md` | unknown | Audit integration tests |
| 132 | `PHASE132_RESILIENCE_SELF_HEALING.md` | unknown | Audit implementation/tests |
| 133–135 | No phase documents found | unknown | Historical scope/classification required |
| 136 | `PHASE136_SELF_UPGRADE_PIPELINE.md` | blocked | Phase 137 isolation acceptance remains a separate release gate |
| 137 | No phase document found in current filename inventory | blocked | Issues #317 and #331 require dedicated rootless Podman evidence and independent review |
| 138 | `PHASE138_EVIDENCE_BACKED_SPECIALIST_CAPABILITIES.md` | unknown | Audit implementation/tests |
| 139–140 | No phase documents found | unknown | Historical scope/classification required |
| 141 | `PHASE141_BOUNDED_AGENT_EXECUTION_LOOP.md` | unknown | Audit implementation/tests |
| 142 | `PHASE142_DURABLE_AGENT_TASK_JOURNAL.md` | unknown | Audit persistence/recovery tests |
| 143 | `PHASE143_WORKER_LEASE_COORDINATION.md` | unknown | Audit concurrency tests |
| 144 | No phase document found in current filename inventory | blocked | Issue #327 records rootless Docker isolation acceptance as outstanding |
| 145 | `PHASE145_PROVIDER_AGENT_LOOP_INTEGRATION.md` | unknown | Audit integration tests |
| 146 | `PHASE146_LOCAL_OLLAMA_AGENT_EXECUTION.md` | unknown | Audit runtime tests |
| 147 | `PHASE147_SELECTED_PROVIDER_AGENT_COMPOSITION.md` | unknown | Audit implementation/tests |
| 148 | `PHASE148_PERSISTENT_CONVERSATIONS.md` | unknown | Audit persistence tests |
| 149 | `PHASE149_AUTHENTICATED_CONVERSATION_API.md` | in_progress | Request identity propagation has recent CI corrections; exact-head CI and end-to-end review still required |
| 150 | `PHASE150_GENERATION_LEASES.md` | unknown | Audit concurrency/cancellation tests |
| 151 | `PHASE151_STREAMING_RESPONSES.md` | unknown | Audit streaming tests |
| 152 | `PHASE152_PROVIDER_NATIVE_STREAMING.md` | unknown | Audit provider integration tests |
| 153 | `PHASE153_OLLAMA_NATIVE_STREAMING.md` | unknown | Audit provider integration tests |
| 154 | No phase document found | unknown | Explicit blocker; scope must be recovered or intentionally reserved with maintainer rationale |
| 155 | `PHASE155_GENERATION_CANCELLATION.md` | unknown | Audit cancellation tests |
| 156 | `PHASE156_DURABLE_GENERATION_RECOVERY.md` | unknown | Audit crash/recovery tests |
| 157 | `PHASE157_POSTGRES_CONVERSATION_STORAGE.md` | unknown | Audit PostgreSQL integration evidence |
| 158 | `PHASE158_APPLICATION_RUNTIME_WIRING.md` | unknown | Audit deployment/runtime tests |
| 159 | `PHASE159_AUTHENTICATED_WEB_CHAT.md` | unknown | Audit end-to-end auth and chat tests |
| 160 | `PHASE160_PROVIDER_STATUS.md` | unknown | Audit provider health semantics |
| 161 | `PHASE161_PROVIDER_SWITCHING.md` | in_progress | Document explicitly requires shared multi-worker control plane, durable actor-attributed audit, connectivity probes, credential rotation procedure |

## Reconciliation decisions

### Duplicate Phase 62 documents

Both files describe the same Hugging Face discovery phase and overlap in purpose. Preserve both historical files for now; do not delete either in this audit-only change.

- `PHASE62_HUGGINGFACE_MODEL_DISCOVERY.md` is the detailed implementation/security contract: bounded tasks/sort/limits, timeout and response caps, token handling, review-first policy, and mocked tests.
- `PHASE62_HUGGINGFACE_DISCOVERY.md` is the concise overview and operational entry point.

Treat the detailed document as the canonical technical specification and the shorter document as its overview. A later documentation-only cleanup may add a pointer between them, but must not discard unique requirements.

### Phase 154

No Phase 154 document or matching scope was found in the initial repository issue/commit search recorded by issue #496. This ledger deliberately leaves Phase 154 `unknown`. Do not invent a title, mark it reserved, or proceed to Phase 162 until maintainers recover its intent or explicitly approve a reservation with rationale.

### Phase 137 and 144

Absence of a `PHASE137_*.md` or `PHASE144_*.md` file is not proof those phases never existed. Issues #317, #331, and #327 contain acceptance criteria and external runtime gates. Keep them blocked until the required dedicated runner/container evidence and independent review are attached.

## Known open acceptance gates (not exhaustive)

| Gate | Evidence | Status / constraint |
|---|---|---|
| Phase 2 approval audit/logging | PR #498 | Open draft; exact-head CI and independent security review required |
| Phase 95 auth DB permissions | Issue #269 | Open security follow-up |
| Phase 137 rootless Podman | Issues #317, #331 | Dedicated dispatch and independent review required; ordinary CI is insufficient |
| Phase 144 rootless Docker | Issue #327 | Trusted runner and immutable worker image evidence required |
| Durable task idempotency/recovery | Issue #345 | Open acceptance criteria; task engine remains coordination only |
| Local persistence concurrency/failure semantics | Issues #338, #339 | Open acceptance criteria; autonomous promotion must remain disabled |
| Desktop onboarding/release | Issues #347, #367 | Separate product/release workstream |
| Agent Skills compatibility | Issue #368 | GitHub install/provenance/resource support remains incomplete |
| Shared provider control plane | Issue #495 | Phase 162 proposal; blocked by serial-integrity gate and prior acceptance work |

## Audit plan

1. Recover historical serials 1–45 and every other undocumented gap from git history, old issues/PRs, and project conversation records.
2. Read each existing phase document and map its claims to concrete implementation paths and tests.
3. Inspect exact CI runs/commits for every claimed completion; distinguish skipped environment-gated tests from executed tests.
4. Reconcile moved phases and link successor serials without losing acceptance criteria.
5. Reconcile Phase 62 references and recover Phase 154 intent.
6. Update `ROADMAP.md` only after the ledger evidence is reviewed.
7. Keep Phase 162 blocked until Phase 154 is resolved and the earlier open gates have been triaged.

## Current sequencing decision

**Do not start Phase 162 or Phase 163 yet.** The earliest actionable work is the historical evidence audit and classification of unresolved serials, beginning with Phase 154 and the open prior security/runtime gates. This document is an initial inventory, not a claim that the audit is complete.
