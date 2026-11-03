# GitHub Actions evidence and model review adapters

This branch adds two adapters used by the evolution review pipeline.

## GitHub Actions evidence

`automation.github_actions_evidence.GitHubActionsEvidenceAdapter` converts
workflow runs obtained from an authenticated GitHub API client into the six
`CheckResult` entries expected by `EvolutionReviewVerifier`.

The caller must configure an exact workflow-name mapping for each category:
`unit_tests`, `integration_tests`, `static_analysis`,
`dependency_audit`, `secret_scan`, and `security_tests`.

The adapter requires the exact target commit SHA, an allowed event, a
completed status, and a successful conclusion. It selects the newest run
attempt for each configured workflow so a newer failed attempt cannot be
masked by an older success. Missing, stale, queued, in-progress, failed,
unknown-event, and malformed evidence fails closed.

Example integration:

```python
adapter = GitHubActionsEvidenceAdapter(
    authenticated_github_client.list_workflow_runs_for_commit,
    workflow_map={
        "unit_tests": ("Yuki quality gates",),
        "integration_tests": ("Phase 93 engineering workflow",),
        "static_analysis": ("Phase 82 capability contract gate",),
        "dependency_audit": ("Dependency manifest audit",),
        "secret_scan": ("Secret scanning",),
        "security_tests": ("Built-in authentication security tests",),
    },
)
report = adapter.build_report(expected_commit_sha)
```

The names above are examples only. Configure them to match workflows that
actually exist and cover the intended category. A workflow name alone does
not prove that its steps perform the claimed scan. The API fetcher must use
authenticated GitHub credentials, paginate or otherwise establish complete
results, and return the workflow-run fields documented by GitHub. Do not
pass model- or agent-supplied run objects to this adapter. GitHub Actions
workflow runs are not equivalent to signed attestations of scanner output.

## Model reviewer

`automation.model_review_adapter.ModelReviewAdapter` asks a configured
provider implementing `generate(prompt)` for a strict JSON verdict and
findings. It binds the result to the supplied commit SHA and stable provider
identity, rejects malformed or oversized responses, and validates the
result using `EvolutionReviewVerifier`.

The patch is explicitly treated as untrusted prompt input. The model is an
AI reviewer, not an authenticated human reviewer and not a CI evidence
source. Its output must not be used as human approval, and the provider's
identity label is configuration rather than cryptographic attestation.
For high-risk changes, retain a separate authenticated human review.

## Tests

```bash
python -m unittest tests.test_github_actions_evidence
python -m unittest tests.test_model_review_adapter
```

Tests use fake clients and providers. Production readiness still requires
an authenticated GitHub API implementation, verified workflow coverage,
model-provider configuration, rate-limit handling, audit persistence, and
integration tests against a test repository.


## Concrete GitHub CLI fetcher

`automation.github_cli_workflow_runs.GitHubCLIWorkflowRunClient` provides a
concrete authenticated fetcher using the operator's installed `gh` CLI. It
uses `gh api --paginate`, requests up to 100 runs per page, bounds total
output and run count, avoids a shell, and never prints CLI stderr or tokens.

```python
from automation.github_actions_evidence import GitHubActionsEvidenceAdapter
from automation.github_cli_workflow_runs import GitHubCLIWorkflowRunClient

fetcher = GitHubCLIWorkflowRunClient("owner/repository")
evidence = GitHubActionsEvidenceAdapter(
    fetcher,
    workflow_map=YOUR_EXACT_WORKFLOW_MAP,
)
report = evidence.build_report(commit_sha)
```

The operator must first authenticate `gh` with access to Actions metadata.
The exact workflow map must reflect workflows actually enabled in the target
repository. The example is a wiring pattern, not a claim that all six
verification categories are already covered by Yuki's current workflows.
