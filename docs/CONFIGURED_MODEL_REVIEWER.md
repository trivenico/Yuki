# Configured Model Reviewer

`automation.configured_model_reviewer.create_configured_model_reviewer`
connects the structured `ModelReviewAdapter` to Yuki's existing
`providers.factory.create_provider` implementation.

## Example

```python
from automation.configured_model_reviewer import create_configured_model_reviewer

reviewer = create_configured_model_reviewer(
    reviewer_id="yuki-independent-review",
    config={"provider": "ollama", "ollama_model": "your-reviewed-model"},
)
evidence = reviewer.review(
    author_id="change-author",
    commit_sha=commit_sha,
    patch=bounded_patch_text,
)
```

The helper requires an explicit stable reviewer identity, constructs only the
provider selected by the existing factory, rejects the test-only mock
provider, and requires `is_available() is True`. It does not silently fall
back to another model or provider.

## Trust and safety boundaries

- Model output remains untrusted structured review evidence.
- An AI reviewer is not a human approver and cannot authorize execution,
  merging, or deployment.
- The reviewer identity is a configured label, not cryptographic identity.
- The provider factory remains responsible for credential handling.
- Readiness does not guarantee inference success; provider errors fail closed.
- Do not send secrets, private keys, credentials, or unnecessary personal data
  in patches or prompts.
- Human review and repository branch protection remain authoritative.

## Tests

```bash
python -m unittest tests.test_configured_model_reviewer
```

Tests use patched providers and do not make network requests.
