# Phase 251 — Calibration expiry and requalification policy

## Scope

This phase makes calibration freshness explicit and enforceable for external reviewers. Historical gold results remain immutable evidence; freshness is evaluated separately from accuracy. The owner can configure per-project minimum gold sample count, passing accuracy, and maximum evidence age.

## Defaults and bounds

- Minimum gold samples: 5 (configurable 1–100).
- Passing accuracy: 0.80 (configurable 0.50–1.00).
- Maximum evidence age: 90 days (configurable 1–3,650).
- The most recent gold-result timestamp determines evidence age.
- A worker with sufficient accuracy but stale evidence is reported as `expired`, not `needs_review`.
- Insufficient sample evidence remains `insufficient_evidence`; poor accuracy remains `needs_review`.

## Enforcement

Non-owner reviewers must satisfy the project's current sample, accuracy, and freshness policy inside the same SQLite immediate transaction that performs adjudication. Expired evidence fails closed with `reviewer_calibration_expired`. Project owners retain their existing owner adjudication authority.

The authenticated owner API exposes:
- `GET /v1/annotation/projects/{project_id}/calibration-policy`
- `PUT /v1/annotation/projects/{project_id}/calibration-policy`
- Existing `GET /v1/annotation/projects/{project_id}/calibration-report` now reports freshness and expiry.

The annotation workbench exposes policy editing and evidence age/status. Policy changes are audited.

## Safety and limitations

- No historical gold result or completed annotation is deleted or rewritten.
- Expiry blocks future reviewer adjudication; it does not invalidate prior decisions, suspend annotators, alter pay, or discard completed work.
- The policy applies to reviewer calibration enforcement. Worker assignment routing and automatic requalification scheduling are not introduced here.
- Requalification must use blinded gold-standard tasks and the existing authenticated assignment lifecycle; it is a subsequent phase.
- Expiry uses the latest gold-result timestamp and a fixed age threshold. No adaptive decay model or role-specific policy is included.

## Validation

Added tests for expiry classification, fail-closed adjudication, policy validation, and owner isolation. Exact-head CI is required before this phase can be considered validated. No local test run is claimed.


## Next phase

Phase 252 adds owner-launched blinded requalification sessions. Session creation must use only registered project gold tasks, create fresh assignment rows atomically, bind each assignment and result to an immutable session identifier, and never expose expected labels in annotator-facing responses. Session results must be evaluated independently from historical calibration evidence. See the Phase 252 design in the project plan.
