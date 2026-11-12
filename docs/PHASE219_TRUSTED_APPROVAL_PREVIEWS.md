# Phase 219 — Trusted approval previews and blind-approval prevention

## Implemented

- Added `CapabilityApprovalPreviewRegistry`, an allowlist of application-owned preview renderers keyed by exact capability identifiers.
- Renderers receive a validated `PolicyRequest` and must return a bounded `ApprovalPreview` with title, operation, target and impact. Output is validated for non-empty printable text and strict length bounds.
- No LLM-generated summaries, client-supplied descriptions, task payload summaries, raw arguments, argument digests or claim digests are used as preview text.
- The durable approval inbox includes the trusted preview and an explicit `preview_available` indicator. The inbox remains redacted.
- The authenticated approval service refuses to approve when the exact capability has no registered trusted preview. It fails before token minting or delivery.
- Added tests for preview rendering, inbox exposure, and rejection of blind approvals when no renderer is registered.

## Integration requirements

Each side-effecting capability must register its own renderer at application composition time. A generic fallback preview is intentionally not provided. If no renderer is registered, the owner can inspect the request metadata but cannot approve it.

Preview renderers are trusted code and must be reviewed with their capability adapter. The current `PolicyRequest` intentionally carries an argument digest rather than raw arguments. Therefore renderers must not pretend to describe argument-level changes they cannot inspect. The current preview is capability/target-level context; a later typed-operation contract is required for exact file diffs, message recipients/content, device commands or financial amounts.

## Remaining work

- Typed, capability-owned preview projections for concrete operation details.
- Owner-facing approval UI (this phase adds API data, not a desktop/web UI).
- Persisted preview snapshot/version binding so reviewers see exactly the preview validated at decision time.
- Approval audit pagination, filters and retention policy.
- Delivery retry/outbox semantics and multi-process race tests.
- KMS-backed encryption, production service deployment review and release certification.

No local tests were run here. CI must pass on the exact PR head. This phase is not production certification.
