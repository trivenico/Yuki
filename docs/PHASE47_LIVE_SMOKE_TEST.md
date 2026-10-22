# Phase 47 — Real inference smoke test

## Why
Provider construction and `/api/tags` availability do not prove that Yuki can generate a response. The release process needs a deliberate, small end-to-end test of the configured provider.

## Run

```bash
python -m tools.provider_smoke_test --confirm-live-request
python -m tools.provider_smoke_test --confirm-live-request --json
```

The explicit flag is required because this sends a prompt to the configured model/provider. Do not include secrets or personal data in the prompt. The default prompt is a fixed, harmless connectivity check. The response body is not printed or stored; only status, elapsed time and character count are reported. The command exits non-zero for mock providers, unavailable providers, empty responses and provider errors.

For local Ollama, configure `provider: ollama`, set `ollama_model` or `YUKI_OLLAMA_MODEL`, install/pull that model separately, then run the command. The command does not download models or start Ollama.

## Important limitations
- This proves one request returned non-empty text, not that the answer is correct, safe, fast enough for production, or tool-enabled.
- A hard deadline must be enforced by the provider adapter. The smoke test cannot safely kill an arbitrary synchronous Python call that hangs.
- Run the normal unit suite and security review separately. Do not paste API keys, cookies, access tokens, or private prompts into logs.
- No live inference was run by the GitHub editing session; tests use fake providers and need CI confirmation.