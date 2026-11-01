# Independent Verification Engine

## Purpose

`automation.independent_verification` adds a read-only, evidence-bounded second
opinion for engineering deliverables. It is deliberately separate from the
implementation agent and does not modify the workspace, run tests, invoke tools,
approve changes, or deploy software.

## Contract

- The caller supplies bounded requirements, a JSON-compatible artifact summary,
  evidence records and the actual test outcome.
- Artifact data is canonicalized and SHA-256 hashed. The report records that
  digest so callers can associate an assessment with the exact reviewed summary.
- Reviewer and author identities must differ. The application wiring must also
  ensure they are genuinely distinct model sessions/providers; distinct labels
  alone are not proof of model independence.
- The reviewer is told that all supplied content is untrusted data and must
  return a strict JSON schema. Invalid, oversized or malformed output fails
  closed.
- A failed test run cannot be overridden by the reviewer. Critical/high findings
  cannot result in an approved report. Approval with no evidence is downgraded
  to inconclusive.
- Every report is advisory and explicitly requires human approval. It is never
  an authorization token and must not be used to bypass existing approval gates.

## Example

    from automation.independent_verification import IndependentVerificationEngine

    verifier = IndependentVerificationEngine(
        reviewer_adapter,
        reviewer_id="independent-reviewer",
        author_id="implementation-agent",
    )
    report = verifier.review(
        requirements=["Reject unauthorized workspace writes."],
        artifact={"change_summary": "Added authorization guard"},
        evidence=[{
            "kind": "test",
            "description": "authorization regression suite",
            "result": "passed",
        }],
        tests_passed=True,
    )

    if report.status != "approved":
        # Keep the existing human review / remediation path.
        ...

The example assumes `reviewer_adapter` is configured independently from the
implementation agent. Never send secrets, credentials, private user data or
unredacted production artifacts to a cloud reviewer. Apply the organization's
data-classification and provider-approval policy before constructing the
artifact summary.

## Limitations and production integration

This component does not itself inspect Git diffs, execute test suites, verify
CI provenance, enforce model diversity, persist reports, or gate deployments.
Its output is a model-generated assessment of caller-supplied evidence, not
cryptographic proof that claims are true. The caller must collect evidence from
trusted systems and bind it to the same commit/artifact digest.

Before production use, wire it to authenticated agent identities, an approved
independent provider, redaction/data-loss controls, durable audit storage,
human review UI and policy enforcement at the merge/deploy boundary. Do not
treat a reviewer response as permission to perform consequential actions.
