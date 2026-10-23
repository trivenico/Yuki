# Phase 140 — Open PR triage snapshot

Snapshot date: 2026-10-23. This is a point-in-time GitHub API review, not a substitute for repository CI or an independent security review.

## Verified decisions

| PR | Current GitHub state | Decision | Evidence / next step |
|---|---|---|---|
| #249 — doctor script import path | Merged | Complete as a merge action | Squash merge commit `f9724212c547d5e0fee06a5296b383e57dcf1191`. The combined-status lookup for its pre-merge head returned no status entries, so the merge is not described as CI-verified here. |
| #265 — Phase 140 integration audit | Open, draft | Keep draft | Documentation-only audit PR. Its head's combined-status lookup returned no status entries and no review submissions/threads were returned. |
| #264 — ASGI auth and CSRF | Open, not draft; mergeable according to GitHub | Hold | PR description says host server/login wiring and terminal CLI login remain follow-ups. No review submissions or inline threads were returned by the connector; no status entries were returned for the inspected head. Require integration proof and exact-head checks before merge. |
| #29 — approval/audit security remediation | Open, draft; mergeable according to GitHub | Keep draft | Description calls for independent security review and cross-platform CI. No review submissions/inline threads were returned; no status entries were returned for the inspected head. |
| #17 — Jules provider/device/approval changes | Open, not draft; GitHub reports not mergeable | Do not merge | Historical broad change set with prior security concerns. No review submissions/inline threads were returned by the connector. Resolve mergeability, inspect the complete diff against current main, and isolate safe changes into small PRs before reconsidering. |
| #234 — Textual requirement change | Open, not draft; mergeable according to GitHub | Hold as potentially superseded | It changes the allowed Textual version to `>=8.2.8,<9.0`. PR #246, a consolidated dependency-maintenance PR, is already merged. Compare current dependency declarations and test supported Python/platform combinations before deciding whether #234 adds anything not already delivered. Do not merge a duplicate dependency change blindly. |
| #72 — authoritative GitHub PR check gate | Open, not draft; mergeable according to GitHub | Hold for dependency review | Useful as a release gate, but application-level logic must not be mistaken for repository branch protection. Validate exact-SHA evidence, trusted source of required checks, and human-approval handling. |
| #218 — supervised durable worker service | Open, draft | Keep draft | Service lifecycle, authentication, durable state, shutdown and deployment behavior need review before enabling a long-running worker. |
| #207 — provider adapters | Open, draft | Keep draft | Description is empty in the inspected snapshot; inspect the full diff, tests, supported providers and secret-handling before review. |
| #190 — repository audit/static CI gates | Open, not draft; GitHub reports not mergeable | Hold | Resolve branch conflicts and verify that static gates run on the intended paths/platforms without becoming bypassable or falsely passing. |
| #191 — provider registry/runtime factory | Open, not draft; GitHub reports not mergeable | Hold | Resolve branch conflicts; verify capability declarations match real runtime behavior and fail closed for unknown provider types. |
| #187 — task-aware model routing/provider catalog | Open, not draft; GitHub reports not mergeable | Hold | Resolve branch conflicts and verify provider allow-lists, data disclosure, deadlines, quota errors, and fallback behavior. |
| #185 — provider deadlines/account sign-in readiness | Open, not draft; mergeable according to GitHub | Hold for checks and diff review | Verify deadlines cover connection/read/write phases, cancellation is bounded, and documentation does not imply unsupported subscription sign-in. |

## Verification limitations

- The GitHub connector returned no combined-status entries for the inspected heads of #249, #264, #265 and #29. An empty response is not proof that CI is disabled or that tests failed; it is insufficient evidence to call the heads green.
- The connector returned no review submissions or inline review threads for #17, #29, #234 and #264 at the time of inspection. That does not replace a human security review.
- GitHub's `mergeable` field is a conflict/mergeability signal, not proof of correctness, passing checks, or security approval.
- Open PR count is not a measure of shipped functionality. Check the exact current main branch before claiming that a capability is integrated.

## Merge policy for this snapshot

Merge only small, non-security-sensitive changes after inspecting their complete diff and verifying required checks for the exact head SHA. For security-sensitive PRs, require independent review and tests that exercise real integration boundaries. Do not bulk-merge historical phase PRs, do not merge PR #17 in its current state, and do not use a missing status response as a green check.

## Next execution order

1. Fetch current main SHA and revalidate the merged state of #249.
2. Determine whether #234 is redundant after #246 by comparing dependency files and diffs.
3. Review #264's changed files and add/verify actual host-server/login integration before merge.
4. Keep #29 draft until independent review and required cross-platform checks exist.
5. Resolve conflicts in #190/#191/#187 by rebasing or recreating focused current-main PRs, not by bypassing conflicts.
6. Run the complete supported test matrix on a release-candidate SHA and attach the resulting evidence.
