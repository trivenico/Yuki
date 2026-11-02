# Phase 138 — Evidence-backed specialist capability reporting

## Scope

Align the canonical capability manifest with specialist runtime components already present in the repository. The coordinator, provider-backed worker, and authorized read-tool adapter are classified as **experimental**, not implemented or planned.

## Contracts

- `specialist-orchestration`: bounded durable plans, capability routing, dependency-gated task dispatch, and trusted authorization.
- `specialist-worker`: explicitly registered provider-backed text-only handlers, durable task leases, approval checks, and bounded artifacts.
- `specialist-read-tools`: explicit list/read/search calls through the authorized tool registry and trusted principal resolver.

## Limitations retained

These capabilities are not production-complete. There is no hosted orchestration API, autonomous scheduler, distributed worker fleet, general autonomous tool-calling loop, or workspace write/shell execution through the specialist adapters. The manifest must not imply those features exist.

## Validation

The manifest validator checks every evidence path exists. Regression tests assert the experimental classifications, entrypoints, test evidence, and key read-only limitation. CI must pass on the exact pull-request head before merge.
