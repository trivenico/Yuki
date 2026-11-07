# Phase 288 — Artifact-bound reviewer task and finding contracts

## Implemented

Added `automation/reviewer_contracts.py` with immutable, typed review records:

- `ReviewTask` binds a task to one artifact SHA-256, author, distinct reviewer, supported review check, bounded requirements, and evidence references.
- `ReviewFinding` records a stable finding ID, severity, state, title, detail, evidence reference, and optional recommendation.
- `ReviewResult` binds the result back to the exact task ID, artifact digest, reviewer identity, and check name.
- `build_review_result` constructs and validates a result against its task.

Validation rejects malformed identifiers/digests, unsupported checks, duplicate requirements/finding IDs, invalid enums, oversized fields, author self-review, and result rebinding to another artifact or reviewer task.

A result marked `passed` is rejected if it contains any open high/critical finding or any open medium finding. Low-severity open findings may remain disclosed in a passed result; downstream acceptance policy can be made stricter. The contract does not silently convert findings into acceptance decisions.

## Trust boundaries

These are data contracts, not an execution engine or authentication system. They do not prove that a reviewer inspected source, that evidence references are genuine, or that the reviewer is authorized. Existing `VerificationEvidenceStore` authorization remains mandatory. No built-in reviewer agents were fabricated and no review execution was dispatched.

## Tests and verification

Added `tests/test_reviewer_contracts.py` covering successful binding, self-review rejection, artifact-rebinding rejection, severity gates, duplicate finding IDs, and unsupported checks. Tests are authored but have not been executed in this environment. No CI status result is claimed.
