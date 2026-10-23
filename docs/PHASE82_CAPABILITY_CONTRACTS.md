# Phase 82 — Capability contracts and readiness reporting

## Purpose

Yuki has many skills and modules, but a module existing is not proof that the feature works end to end. This phase adds a machine-readable contract for key capabilities, conservative status labels, explicit permissions and side effects, supported deployment modes, limitations, and concrete test-evidence paths.

## Status meanings

- **implemented** — a narrow, named contract has tests and its advertised behavior is covered by those tests. This does not mean the whole product is production-ready.
- **experimental** — a bounded implementation exists but end-to-end, operational, or integration guarantees are incomplete.
- **planned** — the capability is not verified as implemented end to end.
- **disabled** — intentionally unavailable in the current build/configuration.

## Source of truth

- Canonical contracts: `core/capability_manifest.py`
- JSON-friendly readiness API: `core.capabilities.capability_readiness()` and `capability_readiness_json()`
- Contract validation: `validate_manifest()`
- Regression tests: `tests/test_capability_manifest.py`
- Existing source audit: `docs/UPSTREAM_CAPABILITY_AUDIT.md`

The readiness API reports a registered-skill count only as inventory metadata. It never executes skills and never infers that a capability is ready because its source file or skill name exists.

## Current audited labels

Implemented narrow contracts: validated skill registry, durable workflow approval boundary, and built-in account/session service. Experimental: bounded model-guided runtime, local lexical memory, bounded workflow runner, allow-listed web page reading, restricted MCP client primitive. Planned: general desktop control, real-time interruptible voice, complete specialist-agent orchestration, and hosted HTTP authentication integration.

These labels are deliberately conservative. Review and update them when end-to-end tests or architecture change; do not promote a status based only on a merged PR.

## CI gate

The dedicated GitHub Actions workflow validates schema, unique IDs, allowed statuses/risks/deployment targets, test-evidence existence for every implemented claim, and JSON serialization across Ubuntu, Windows, and macOS on Python 3.11.
