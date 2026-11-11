# Phase 211 — Restore trusted skill policy context contract

## Purpose

Restore the typed provenance value object required by the earlier approval broker while retaining the claim-fenced runtime introduced in phases 209–210.

## Implementation

- Reintroduces immutable `SkillPolicyContext` in `core.guarded_skill_runtime`.
- Validates principal, trigger, goal and task identifiers as non-empty, trimmed, bounded strings.
- Documents the trust boundary: values must be derived by a trusted worker from authenticated durable records, never copied from untrusted task payloads.
- Adds regression coverage for valid provenance and malformed, blank, whitespace-padded, oversized and non-string fields.

## Security boundary

This value object validates field shape; it does not authenticate a principal or prove ownership of a goal/task. The durable worker and authenticated task service must construct it from trusted records. The lease-bound resolver must continue validating the task claim before and after credential lookup.

## Not complete

This phase does not restore the broker or gateway, does not deliver credentials, and does not connect policy evaluation to skill dispatch. It does not solve remote provider commit fencing, KMS key custody/rotation, durable cross-process revocation, or recovery of an unresponsive worker.

## Acceptance

- Cross-platform CI on the exact PR head.
- Regression tests verify strict field validation.
- Follow-on broker integration must prove owner/task/worker/claim-generation binding and preserve final execution-guard checks.
