# Model evaluation gate (Phase 43)

This phase adds deterministic offline checks for observable responses from an injected model adapter. It does not download or execute model artifacts and cannot prove model safety or general intelligence.

## Example

```python
from evaluation.model_eval import EvalCase, ModelEvaluator
report = ModelEvaluator(minimum_pass_rate=0.9).run(
    "candidate-revision-id", candidate_adapter.generate,
    [EvalCase("python_mentions", "coding", "Explain Python", required_terms=("python",))],
)
print(report.to_json())
```

## Boundaries

- Use small deterministic suites and adapters with real request deadlines. Python cannot preempt a synchronous callable that blocks indefinitely; the suite deadline is observational only.
- Reports omit prompts and exception messages to reduce accidental data disclosure.
- Substring assertions are a baseline, not semantic evaluation. Add curated benchmark datasets and task-specific metrics before using scores for important decisions.
- License approval, security review, compatibility checks and human approval are independent gates before a candidate may enter a staged trial.
- Staged-trial eligibility is not production promotion. This module never changes routing, installs models, or grants tools.

## Run tests

`python -m unittest tests.test_model_eval -v`

Tests are added in this PR but must be executed in CI before merge.
