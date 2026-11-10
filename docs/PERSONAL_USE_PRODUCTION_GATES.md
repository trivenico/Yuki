# Personal-use preview and production release gates

## Intended deployment

**Yuki is a private, single-owner personal assistant project**: the goal is a JARVIS-like assistant for its owner, not a public SaaS, multi-tenant service, or product offered to other users. Use the owner's own machine and accounts, with owner-controlled permissions and explicit confirmation for consequential actions. Do not publish it or enable remote access during development.

The JARVIS / StonicAI comparison describes the desired personal-assistant experience and quality bar; it is not evidence that Yuki already has equivalent capabilities, autonomy, integrations, reliability, or security assurance. Production qualification is still required even for personal use, because a local agent can affect private files, accounts, devices, and systems.

## Current safe-use boundary

- Run locally under the owner's operating-system account; treat the owner as the only user and do not add public registration, shared accounts, or multi-tenant assumptions.
- Keep MCP bound to loopback. Do not publish it, tunnel it, reverse-proxy it, or bind it to a LAN/public interface. No other person should have network access to the assistant during preview.
- Keep cloud providers and integrations explicitly configured; prefer local Ollama/mock mode for smoke tests and never send private prompts to cloud providers without explicit choice.
- Keep optional conversation memory disabled unless needed for a local test. Session IDs are not authenticated identities.
- Keep plugin discovery and untrusted skill imports disabled. A Python plugin is code execution with the process user's permissions, not a sandbox.
- Keep firewall changes, device controls, messages, file writes, deployments, merges, and other consequential actions behind explicit per-action authorization and safe defaults.
- Do not store provider tokens in source control or plaintext committed configuration.

## Required production release gates

A release must not be labeled production-ready until evidence exists for each gate:

1. **Reproducible quality:** clean install from documented steps; full unit/integration suite passes on supported OS/Python combinations; exact release commit has required CI checks green.
2. **Authentication and authorization:** session identity is derived server-side; every sensitive action has least-privilege authorization; CSRF/session protections are integrated into actual host entry points; no caller-supplied identity claims are trusted.
3. **Sandboxing:** untrusted plugins, model-generated code, shell commands and file operations are isolated from the host; resource/time/network/filesystem limits are tested adversarially. A registry allow-list alone is not a sandbox.
4. **Task safety and recovery:** durable checkpoints; bounded retries; cancellation; idempotency keys where supported; crash-injection tests before and after external side effects; ambiguous outcomes require reconciliation instead of replay.
5. **Audit and observability:** redacted structured logs, durable audit failure policy, secret redaction tests, bounded metrics, health/readiness probes, useful diagnostics and documented retention.
6. **Provider controls:** explicit provider/model selection; prompt/data-disclosure consent; bounded deadlines and output sizes; quota/error handling; no hidden cloud fallback; no scraping of browser cookies or consumer web sessions.
7. **Operational resilience:** backup/restore, database migration/rollback, stale worker recovery, disk-full behavior, graceful shutdown, dependency pinning and vulnerability review.
8. **Release/security review:** independent review of security-sensitive diffs; threat model and abuse cases updated; no unresolved critical/high findings without a documented exception and owner.
9. **Real-world acceptance:** manual end-to-end test on the target personal machine and data layout; upgrade/downgrade test; restore drill; explicit release checklist signed off before any remote endpoint is enabled.

## Progress reporting rules

- A merged PR is not proof of production readiness.
- A code path existing is not proof that it is wired into the runtime.
- A missing workflow/status response is not a passing check.
- A unit test is not proof of security isolation or production deployment behavior.
- Report each capability as implemented, integrated, tested, or production-qualified separately.
- Do not publish or enable remote access merely because the MCP bridge is technically reachable.

## Next work sequence

1. Get all required checks green on the exact head of PR #266 and inspect failures.
2. Review bridge request/auth/session behavior and test loopback-only enforcement.
3. Complete server-side identity-bound sessions before enabling persisted conversation memory for any multi-user setting.
4. Audit the actual task execution path, approval binding, and durable recovery against external side effects.
5. Run a full clean-install and supported-platform test matrix.
6. Reassess all gates and publish an evidence-backed readiness report. Keep Yuki local-only until then.
