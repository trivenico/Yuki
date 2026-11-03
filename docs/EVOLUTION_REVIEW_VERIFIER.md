# Evolution review and verification contracts

`automation.evolution_review_verifier` validates independent review evidence and
commit-bound verification results before a change may be proposed for merge.

## Required evidence

The reviewer must have an identity distinct from the change author and review
the exact expected commit SHA. Approve, request-changes, and abstain are
separate verdicts. An approval cannot carry unresolved findings.

The verification report must contain exactly one result for each required
check: unit tests, integration tests, static analysis, dependency audit,
secret scan, and security tests. Every result must reference the exact commit
being considered. Missing, duplicate, unknown, failed, or stale checks block
the gate.

## Scope and trust boundary

The module validates evidence objects; it does not authenticate reviewer
identity, invoke an AI model, run CI, attest that evidence IDs are genuine,
approve a plan, or merge a pull request. Production integration must obtain
review identity from an authenticated provider and check evidence IDs against
trusted GitHub Actions / scanner APIs. Never trust an agent-supplied boolean
or self-reported CI result.

The positive decision means only that a merge proposal may be raised. Branch
protection and required human approval remain authoritative. This component
does not merge code or deploy software.

## Tests

```bash
python -m unittest tests.test_evolution_review_verifier
```
