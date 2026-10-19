# Phase 9 — Reliability and integration gap audit

## Changes in this phase
- SQLite task jobs now use expiring worker leases, attempt limits, restart-time stale-lease recovery, and optional unique idempotency keys.
- Task payloads must be JSON objects and timezone-naive scheduling timestamps are rejected.
- Database and parent directory permissions are restricted where the OS supports POSIX modes.
- Cron now supports comma lists, ranges, and steps, plus durable per-minute enqueue deduplication through idempotency keys.
- Provider calls reject HTTP redirects to prevent credentials and tool payloads from being forwarded to another origin.
- Regression coverage checks queue idempotency, worker identity binding, stale lease recovery, and expanded cron syntax.

## Gaps still open
1. No exactly-once side effects. Queue delivery is at-least-once. Each handler must use downstream idempotency or transactional outbox patterns.
2. Cron semantics are deliberately bounded. Day-of-month and day-of-week use AND semantics; there are no timezone-per-job, @daily aliases, or persisted schedule definitions. Keep schedules in application config and test DST/UTC expectations.
3. Provider schemas. Only the OpenAI Chat Completions tool schema is implemented. Anthropic Messages API and provider-specific retries, rate limits, and token accounting need dedicated adapters.
4. Runtime wiring. ReAct, queue, onboarding, messaging, and skill staging must be connected to the existing CLI/TUI/runtime through the same authorization policy before exposing them to users.
5. Messaging. Generic HMAC is not a replacement for Slack signing secrets, Discord interaction verification, Telegram webhook secrets, or WhatsApp provider verification.
6. Skills. Approved SKILL.md files remain declarative data and are not interpreted. Never run generated Python directly; use an OS/container sandbox if code execution is later introduced.
7. Daemon service. Production deployment still requires systemd/launchd/Windows service definitions, shutdown draining, health/readiness endpoints, and operational alerts.
8. Verification. Run the full repository unit suite and integration/security checks on the final PR head. A previous successful run does not validate later commits.

## Release gate
Keep PR #35 in draft until CI has passed on the current head and the integration/security review confirms that the new components are wired through centralized authorization. No automatic cloud fallback, shell access, arbitrary MCP execution, or generated-code execution should be enabled by default.
