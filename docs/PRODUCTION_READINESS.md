# Yuki production-readiness plan

Status checked against the repository on 2026-10-17. A module being present does not imply its integration is complete.

## Priority roadmap

| # | Upgrade | Current state | Acceptance criteria |
|---|---|---|---|
| 1 | OpenTelemetry tracing | Not integrated | Trace provider/model requests, decisions and skill calls; redact sensitive data; export locally or to configured OTLP endpoint; tests for spans and disabled mode |
| 2 | Golden dataset + CI/CD | CI unit-test workflow exists; golden eval set not found | At least 20 deterministic scenarios; regression runner; CI blocks on failures; model-dependent cases use fixed fixtures or explicit opt-in |
| 3 | Externalized prompt manager | Persona currently defined in Python | Versioned YAML schema, validation, immutable prompt versions, controlled experiment assignment and rollback |
| 4 | Circuit breakers + retry budgets | Initial synchronous primitives added in `core/resilience.py` | Apply at provider/tool boundaries, configure real client timeouts, bound retry budgets, test async providers separately |
| 5 | Cost/resource tracking | `core/token_usage.py` exists but README notes provider wiring is incomplete | Connect actual provider usage metadata, label estimates, record latency/session duration and budget alerts |
| 6 | Cryptographic agent identity | Not implemented | Signed identity format, trust registry, key rotation/revocation, verification tests and documented threat model |
| 7 | User documentation portal | Existing README and docs; no complete onboarding portal | Beginner quickstart, configuration, troubleshooting, privacy/security, voice/memory setup and release process |

## Resilience and self-healing foundations

- `core/resilience.py` supplies a thread-safe synchronous circuit breaker, bounded exponential retry decorator, state lock, and decorator helper.
- `core/self_healing.py` supplies explicit health-check registration and recovery handlers with approval, cooldown, retry budget, and post-recovery verification.
- Recovery actions are never auto-discovered. Register only reviewed handlers. Use real network/database timeouts; the circuit breaker cannot interrupt a blocked call.
- The retry decorator must only wrap idempotent operations or operations protected by an idempotency key.
- These foundations are not yet wired into the provider/agent runtime and do not by themselves establish production readiness.
- Async clients need async-aware implementations; do not call synchronous sleep-based retry around an event loop.

## Git workflow

Implement each upgrade on a feature branch, run `python -m unittest discover -s tests -v`, inspect the diff, then open a pull request. Do not merge automatically until CI and human review pass.
