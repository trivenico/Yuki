# Independent agent evaluation

\`automation.agent_evaluation.AgentEvaluationEngine\` records bounded,
independent, evidence-backed reviews and computes a deterministic quorum result.
It does not call models, infer reviewer competence, or treat free-form text as
proof that a change is safe.

## Example

\`\`\`python
engine = AgentEvaluationEngine(
    "state/agent-reviews.sqlite3",
    registered_reviewer_ids={"security", "qa", "architecture"},
    authorize=authorization_policy,
    required_reviews=2,
    minimum_score=3,
)

engine.submit_review(
    actor_id="security",
    reviewer_id="security",
    subject_id="pull-request-42",
    verdict="approve",
    scores={"correctness": 4, "security": 5, "maintainability": 4},
    findings=(),
    idempotency_key="pr-42-security-review-v1",
)

result = engine.consensus(
    actor_id="supervisor", subject_id="pull-request-42",
)
\`\`\`

## Consensus policy

- A reject vote vetoes approval.
- Fewer than the configured independent reviews yields \`awaiting_reviews\`.
- Any revise vote yields \`revision_required\`.
- Approval requires the configured number of approve votes and every submitted
  rubric score to meet the configured minimum.
- Conflicting or insufficient evidence is not converted into approval.
- Scores are averaged per rubric dimension for transparent reporting.

Each registered reviewer may submit at most one review per subject. Review
identity must match the authenticated actor. Submissions are bounded, persisted
in SQLite, idempotent, and protected by an authorization callback. Callback
exceptions fail closed.

## Important limitations

This is a deterministic record-and-quorum engine, not a semantic judge.
Reviewer independence is enforced by distinct registered reviewer identities,
but the application must ensure those identities correspond to genuinely
separate review processes/models and do not share the same untrusted context.
The engine does not establish that a reviewer actually inspected source code,
tests, or runtime evidence. The application must bind reviews to immutable
commit/artifact digests and provide evidence provenance before production use.

The engine does not merge PRs, approve deployments, grant permissions, or
execute reviewer suggestions. Human approval remains required for sensitive
actions.

## Tests

\`\`\`bash
python -m unittest tests.test_agent_evaluation
\`\`\`
