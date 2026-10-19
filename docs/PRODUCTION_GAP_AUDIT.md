# Production engineering gap audit — Phase 18

## Scope and evidence
This audit is based on repository source and draft PRs #12–#18 visible on the inspected GitHub refs. Presence of a module is not evidence of integration or production readiness. Tests authored in this phase have not yet been run locally; CI on the exact PR head is required.

## Critical gaps addressed in this phase
- Approval flow imported by tests but missing as a coherent module set: added manifest validation, manifest planner, allow-listed subprocess executor, capability-gated dispatcher, identity-bound coordinator, and redacted SQLite audit events.
- Confirmation callback mutation risk: the callback receives a JSON deep copy; execution uses the stored snapshot, not the mutable displayed object.
- Approval replay and cross-session use: pending entries are consumed once and bound to actor/session; persistent store is used when injected.
- Unbounded skill process behavior: argv-only `subprocess.run(shell=False)`, bounded timeout, stdout/stderr limits, JSON-only IPC, and generic errors.
- Schema drift: input/output object validation runs before and after subprocess execution.

## Remaining gaps — do not call production-ready
1. Approval identity is only as trustworthy as the caller's actor/session inputs; the CLI/API must authenticate them and never accept identity directly from model output.
2. Persistent audit writes are currently best-effort at the coordinator boundary and need a policy decision for fail-closed behavior if audit storage is unavailable.
3. The dispatcher accepts pre-registered argv; production deployment must ensure registration comes from trusted signed/reviewed configuration, not untrusted plugin metadata.
4. Subprocess output is bounded after `subprocess.run` captures it; an actively hostile child could allocate memory before the post-run limit is checked. Replace with streaming bounded pipe readers / OS resource controls before running untrusted code.
5. No OS sandbox, filesystem/network isolation, CPU/memory quota, secret isolation, or Windows job-object/process-tree termination is implemented.
6. The `entrypoint` string is validated as metadata but does not itself load code; actual execution uses the registered argv. Keep this distinction explicit.
7. Existing `YukiCoreAgent` constructor does not yet accept `dispatcher`; integration with legacy agent and runtime composition remains open.
8. The earlier PR chain has not been reconciled to main. Do not merge children before parents, and do not merge while CI fails or is absent.
9. Runtime worker lease fencing, durable cancellation, output redaction, provider lifecycle, memory lifecycle, skill inventory accuracy, auth/rate limits, deployment smoke tests, and observability need follow-up.

## Production sequence
- P0: restore green CI and fix test/import/contract failures on exact head.
- P1: authenticated actor/session adapter + approval UI/CLI confirmation display.
- P2: isolated subprocess runner with streaming output caps and process-tree termination.
- P3: runtime composition root wiring provider, registry, queue, dispatcher, audit and shutdown.
- P4: end-to-end acceptance, security/privacy review, dependency/secret scanning and clean-install deployment test.

## Release gate
Do not merge or advertise production readiness until the dependency chain is clean, the full suite passes on exact PR head, approval replay/cross-actor tests pass, malicious input tests pass, and deployment smoke tests are recorded.