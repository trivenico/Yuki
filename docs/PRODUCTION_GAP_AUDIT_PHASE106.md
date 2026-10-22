# Production engineering gap audit — Phase 106

Audit date: 2026-10-22
Repository: trivenico/Yuki
Scope: project history through Phase 105, README capability disclosures, current CLI/dashboard wiring, and durable approval/reconciliation modules.

## Verified current baseline

- The Phase 105 CLI reconciliation PR workflow passed: https://github.com/trivenico/Yuki/actions/runs/39712270572
- The repository README explicitly states that Yuki is not production-ready.
- Startup checks `assert_production_runtime_supported(config)` before workspace recovery. The production guard must remain in place until all side-effect entry points use the durable authorization boundary.
- `core/performance_monitor.py` provides best-effort CPU/RAM telemetry, NVIDIA telemetry when `nvidia-smi` exists, recorded token totals, and model-context remaining only when valid context metadata is supplied. Unknown values are intentionally not guessed.
- `core/token_usage.py` tracks only events actually recorded by Yuki. Provider-wide quota and remaining billable tokens are not available unless a provider supplies that metadata.
- The Textual dashboard already refreshes host telemetry. Before this phase it did not expose execution reconciliation.

## Phase 106 change

The Textual dashboard now accepts the existing reconciliation service:
- `/reconcile list` lists pending execution key, state, action hash, and last update.
- `/reconcile resolve KEY DISPOSITION | EVIDENCE` stages a decision for review.
- `/reconcile confirm KEY` is a separate, explicit confirmation step. A mismatched or absent confirmation cancels the pending decision.
- Resolution records evidence through the existing audit-backed service. It does not execute, retry, or mutate the execution ledger state.
- `main.py` passes the existing SQLite reconciliation service into the dashboard.

This is a Textual desktop/supervision UI, not a separate native desktop application. The confirmation is a second explicit CLI-like dashboard command, not operating-system identity authentication.

## Outstanding P0/P1 gaps

### P0 — Security and execution integrity
1. Inventory every direct side-effect entry point (sandbox, reconnaissance/Nmap, packet capture, firewall, webcam, voice recording, browser/plugin/MCP paths, evolution, filesystem writes, email integrations). Mark each as durable-approval integrated, disabled, or unverified.
2. Add real-store integration tests per operation family: denied approval, audit-store outage, execution-ledger outage, crash between `running` and completion, duplicate/concurrent approvals, and restart recovery.
3. Reconciliation currently records a decision separately from the ledger. It deliberately does not resolve the underlying ledger state or authorize retry; provider-specific evidence and a separately reviewed state-transition policy are still required.
4. Audit event vocabulary should distinguish a reconciliation decision from an execution-uncertain event, and operator identity must come from a trustworthy authenticated identity source for multi-user deployments.
5. Add concurrent-resolution tests and consider atomic coordination between audit and reconciliation persistence; current separate SQLite databases cannot provide a single cross-database transaction.
6. Keep production startup blocked until coverage and integration requirements are satisfied.

### P1 — Performance visibility
1. Keep CPU/RAM/GPU data labeled as host telemetry; GPU support is currently NVIDIA-specific and depends on `nvidia-smi`.
2. Connect provider-reported token usage metadata for each provider adapter and distinguish exact usage from estimates.
3. Integrate model-specific context-window limits and current prompt/context counts. Do not present a generic token counter as provider quota or “remaining tokens.”
4. Add per-model latency, time-to-first-token, tokens/second, request errors, and local model runtime metrics where providers expose them.
5. Add UI tests for refresh behavior, missing telemetry, and safe fallback; add platform tests for Windows/macOS memory and GPU metrics (Linux `/proc/meminfo` is the current dependency-free RAM fallback).

### P1 — Runtime and product completeness
1. README documents that there is no complete conversational LLM response loop, autonomous project builder, production firewall expiry/recovery, or full wake-word/barge-in path.
2. MCP and plugin loading remain security-sensitive and incompletely wired. Discovery must not imply trust or execution permission; plugin imports run with process privileges.
3. Add structured logs, metrics, trace/correlation IDs, secret redaction, log rotation, backup/restore tests, and documented data-retention controls.
4. Define supported OS/Python/provider matrix and test packaging/upgrade/rollback, external-SSD path safety, permissions, and migration compatibility.
5. Add dependency/SBOM/license review and independent security testing before release.

## Exit criteria for production enablement

- All side-effect commands have a documented authorization owner and deny-by-default integration tests.
- Durable audit and execution persistence failures block the relevant side effect.
- Crash recovery is provider-aware and never blindly replays ambiguous external actions.
- Security, platform, dependency, and packaging gates pass in CI.
- Readiness claims are based on recorded test and operational evidence, not module existence.
