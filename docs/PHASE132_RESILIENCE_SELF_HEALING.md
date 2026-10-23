# Phase 132 — Resilience and approval-gated recovery primitives

## Added
- `core/resilience.py`: bounded, explicit health-check registry and sanitized report.
- `core/self_healing.py`: allowlisted recovery proposals with risk labels and a fresh approval callback before executing a registered recovery callback.
- `tests/test_resilience_self_healing.py`: unit tests for health outcomes, sanitized exceptions, duplicate registration, approval denial, missing approval and successful approved execution.

## Security boundaries
- Health checks are registered explicitly by trusted code. They do not restart services or mutate state.
- Recovery callbacks are never discovered from arbitrary files or imported dynamically.
- Missing or failing approval callbacks fail closed.
- A proposal must match a registered action and its registered risk.
- Callback exceptions are summarized by exception type only; raw messages are not returned.
- This is a primitive, not production self-healing: action-specific timeouts, process isolation, durable audit, backup/rollback, health thresholds, startup wiring and OS-level authorization remain integration work.

## Verification
Run:
```sh
python -m unittest tests.test_resilience_self_healing -v
```
Run the repository's full documented test suite and security checks before merge. The GitHub connector cannot execute tests in this commit workflow; green status must not be inferred from the presence of test files.
