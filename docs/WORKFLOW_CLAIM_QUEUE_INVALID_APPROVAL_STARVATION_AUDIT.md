# Workflow Worker Claim: Invalid Approval Rows Can Starve Eligible Tasks

- **Severity:** Medium
- **Area:** Durable workflow queue / worker scheduling
- **Status:** Confirmed by source inspection
- **Scope:** `automation/workflow_store.py`, `tests/test_workflow_store.py`

## Finding

`WorkflowRunStore.claim_next()` selects queued workflow rows ordered by creation time and run ID, but applies a hard SQL limit of 100 before validating approval state:

```sql
SELECT * FROM workflow_runs
WHERE state='queued'
  AND NOT EXISTS (
    SELECT 1 FROM revoked_approval_sessions r
    WHERE r.session_hash=workflow_runs.approved_session_hash
  )
ORDER BY created_at, run_id
LIMIT 100
```

The method then iterates over only those 100 rows in Python and selects the first task that either does not require approval or has a complete, unexpired approval whose digest matches the request digest.

Queued tasks that require approval but are unapproved, have expired approval, or have a mismatched approval digest remain in the queue and are skipped. They are not removed from the candidate set or transitioned to a terminal/blocked state.

Consequently, if the oldest 100 queued rows are all ineligible, every call to `claim_next()` evaluates the same 100 rows and returns `None`. A valid eligible task at position 101 or later is never considered. Repeated supervisor cycles do not advance the queue.

The SQL excludes rows with a revoked approval-session hash, but does not exclude other invalid approval states before applying the limit.

## Impact

An attacker or ordinary workflow activity that creates enough queued tasks requiring approval but leaves them unapproved (or lets approvals expire) can indefinitely starve later eligible tasks. This is a queue availability / scheduling denial of service. The condition can also arise without malicious intent when stale queued work accumulates.

The affected tasks are not executed without approval; the defect is that invalid entries can prevent unrelated eligible work from being claimed.

## Recommended remediation

1. Move eligibility filtering into SQL so the candidate limit applies to claimable tasks, including the no-approval-required case and complete, unexpired, digest-matching, non-revoked approvals.
2. Alternatively, page through queued candidates with a stable cursor until an eligible row is found, with a bounded scan budget and persisted progress.
3. Define a lifecycle policy for stale queued rows (for example, explicit approval-expired or approval-required status) so they do not permanently occupy the active claim queue.
4. Add regression tests with at least 100 ineligible older rows followed by an eligible row, covering unapproved and expired approvals, and verify the eligible row can still be claimed.

## Evidence and limitations

- `WorkflowRunStore.claim_next()` applies `LIMIT 100` in SQL before Python approval validation.
- The Python predicate skips approval-required rows unless approval metadata is complete, unexpired, and digest-matched.
- Skipped rows remain in state `queued`; no cursor or terminal transition advances the candidate window.
- Existing tests cover individual unapproved/expired approvals and successful claims, but do not exercise a queue containing more than 100 ineligible rows ahead of eligible work.

Source-review finding only. No tests, CI, or runtime experiments were executed for this audit pass.
