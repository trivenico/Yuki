# Phase 55 — Provider smoke-test operational gate

## Purpose
The live smoke test is the operator-controlled boundary between unit-test confidence and a real model round trip. CI must never send prompts to a provider account.

## Before a live test
1. Select an explicit provider in the local configuration or through the supported environment override.
2. For Gemini, install the official Gemini CLI and complete its supported account login as the same OS user running Yuki.
3. Confirm the configured model/provider terms permit sending the smoke-test prompt.
4. Run the command only when you consent to transmitting one short prompt.

## Run
```bash
python -m tools.provider_smoke_test --confirm-live-request --json
```

Without `--confirm-live-request`, the command exits before loading the provider or making a network request. The report includes provider name, pass/fail, elapsed time, response character count, and a sanitized detail; it does not include model output or raw exception text.

## Interpreting results
- `pass`: the configured provider reported available and returned non-empty text. This verifies one round trip, not model quality, tool execution, long-running stability, quota headroom, or production readiness.
- `fail`: the provider is unavailable, is mock, returned no usable text, or raised an error. Inspect provider-local logs without copying secrets into issue trackers.
- For Gemini, executable availability alone does not prove account authentication; the actual generation call is the authentication/quota check.

## CI
CI uses fake providers only. No API keys, browser login sessions, or real inference calls are required for unit tests.
