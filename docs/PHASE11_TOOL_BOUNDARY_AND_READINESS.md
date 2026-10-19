# Phase 11 — Tool-boundary validation and readiness score

## Phase 11 fixes
- Reject duplicate, empty, oversized tool-call IDs and oversized serialized arguments before any handler runs.
- Reject non-finite JSON arguments (NaN/Infinity) before execution; enforce a 64 KiB argument envelope.
- Strengthen the bounded JSON-schema subset validator: reject unknown types, malformed schemas, oversized arrays, and non-finite numeric values.
- Add offline regression tests that assert malformed tool-call batches do not execute any handlers.

## Readiness estimate (engineering assessment, not a test result)
Estimated production readiness: **40%**. This score measures evidence for a safe, deployable integrated product—not the number of features or lines of code. Confidence is medium-low because the complete current-head CI suite has not been observed passing in this review.

| Area | Weight | Estimated readiness | Evidence / remaining work |
|---|---:|---:|---|
| Core security and approvals | 20% | 65% | Permission gates, MCP URL policy, approval snapshots; needs end-to-end bypass and concurrency testing |
| Runtime integration | 20% | 35% | `/react` CLI path added; config, MCP, daemon, UI and provider lifecycle not unified |
| Provider ecosystem | 15% | 35% | Ollama and OpenAI-compatible paths; native Anthropic/Ollama tool schemas and provider resilience remain |
| Test and CI evidence | 15% | 30% | Offline regression tests and CI workflow exist; current-head full run is unverified |
| Daemon and scheduling | 10% | 50% | SQLite queue, leases, retries and cron; no supervised deployment or exactly-once side effects |
| Skills and learning | 10% | 40% | Hash-bound declarative proposals; not integrated as executable runtime skills, intentionally no generated-code execution |
| UI, messaging, deployment | 10% | 15% | CLI/TUI foundations; real platform adapters, desktop wrapper, service packaging, monitoring are incomplete |

Weighted estimate = 0.20×65 + 0.20×35 + 0.15×35 + 0.15×30 + 0.10×50 + 0.10×40 + 0.10×15 = **40%** (rounded).

## Release blockers
1. Run the full unit suite on this exact commit and fix all failures.
2. Add config migration/compatibility so onboarding JSON cannot silently diverge from runtime YAML; backup and rollback must be tested.
3. Unify authorization across agent, ReAct, MCP, messaging and queued tasks.
4. Implement provider-specific tool-call adapters and test with mocked provider responses.
5. Add platform-specific messaging verification, deployment service definitions, health/readiness endpoints, graceful shutdown, alerts, dependency scanning, and security review.
6. Test concurrent queue workers, cron restart behavior, idempotency, and authorization bypass attempts.

## Release policy
Keep PR #35 in draft. No claim of production readiness is made by this score. Do not enable implicit cloud routing, arbitrary shell access, arbitrary MCP tools, or generated Python execution by default.
