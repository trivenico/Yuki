# Phase 319 — Evidence-gated remediation planning

## Scope

Adds a deterministic planner that converts structured incident findings and
recommendation codes into explicit remediation plans. Plans specify priority,
risk, reversibility, and required evidence. Plans are persisted with explicit
approval/rejection decisions and metadata-only outcome receipts.

## Planning behavior

- Only recommendations present in the supplied intelligence report are
  considered.
- Duplicate recommendation codes are collapsed into one plan.
- Each recognized recommendation maps to a bounded evidence checklist and
  risk classification.
- Unknown recommendation codes receive a conservative high-risk,
  non-reversible classification and a generic evidence requirement.
- New plans always start in the pending state.

## Approval controls

The plan store requires a host-provided authorization callback for create,
approve, reject, and receipt operations. Decisions are transactional and
one-way: only pending plans can be approved or rejected. A rejected plan cannot
receive an execution receipt. A successful receipt moves the plan to executed.

This phase does not include an execution engine. Approval is a prerequisite for
a future separately implemented executor; it is not a command to execute.
No worker, lease, task, dispatch, deployment, firewall, or host configuration
is mutated by this planner.

## Receipt semantics and trust boundary

The current receipt API stores an authorized executor identifier, an outcome,
a SHA-256 evidence digest, and a timestamp. It is an auditable assertion from
the authorized host caller, **not a cryptographically signed or independently
verified execution attestation**. A future executor must generate and verify
signed receipts with host-controlled keys, bind the receipt to plan and action
identifiers, and validate before/after evidence before the product may claim
that remediation was independently verified.

## Data handling

Only plan descriptions, recommendation codes, evidence requirement labels,
actor IDs, bounded reason codes, outcome values, and evidence digests are
stored. No credentials, lease tokens, raw incident logs, or artifact content
are accepted. The database requires protected local storage and backup policy.

## Verification

```bash
python -m unittest tests.test_reviewer_remediation_planner tests.test_reviewer_incident_intelligence tests.test_reviewer_incident_analysis -v
```

Tests cover pending plan creation, evidence requirements, authorization,
single-decision behavior, approval-gated receipts, and rejection handling.
Tests are authored; execution must be confirmed by CI or a local run.
