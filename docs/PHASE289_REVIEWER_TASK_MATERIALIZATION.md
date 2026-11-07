# Phase 289 — Reviewer task materialization

## Implemented

Added `automation/reviewer_workflow.py`. It converts a deterministic `ReviewerTeamPlan` into one immutable `ReviewTask` contract per assignment.

Each task carries the exact artifact SHA-256, author identity, distinct reviewer identity, check name, caller-supplied requirements, and caller-supplied evidence references. Task IDs are deterministic for the artifact/check/reviewer tuple, making repeated materialization stable.

The builder requires requirements and evidence-reference mappings to match the assigned checks exactly. It rejects missing or reused reviewer identities, author self-review, missing criteria, invalid evidence references, and invalid task contracts.

## Important boundaries

This phase materializes contracts only. It does not enqueue tasks into `TaskRuntime`, dispatch agents, expose artifact bytes, authenticate evidence references, or create verification reports. This is intentional: the repository does not yet have a safe end-to-end reviewer worker adapter with a trusted per-agent principal resolver. Connecting task creation before that adapter exists would risk confusing a planned review with a performed review.

The caller must provide review criteria and evidence references. The module does not invent criteria or treat a URI string as proof that evidence exists.

## Tests and verification

Added `tests/test_reviewer_workflow.py` for deterministic task IDs, artifact binding, exact mapping coverage, author/reviewer separation, empty criteria, and invalid plan types. Tests are authored but not executed here. No CI status checks are claimed.
