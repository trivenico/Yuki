# Yuki production readiness gates

Every checked item must link to a real CI run, test artifact, review, or restore record.

## Release gates

- [ ] Unit tests pass on all declared supported Python versions.
- [ ] Full repository import/compile check passes.
- [ ] Lint and type checks pass under pinned tool versions.
- [ ] Coverage report is published; critical policy and authorization paths meet an agreed threshold.
- [ ] Dependency vulnerability and license inventories are reviewed.
- [ ] Security review covers prompt injection, SSRF, secret leakage, path traversal and plugin trust.
- [ ] Risky skills fail closed when sandbox enforcement is missing.
- [ ] External actions require fresh scoped approval and emit audit events.
- [ ] Memory backup, export, restore, deletion and retention are tested.
- [ ] Job restart recovery, retry/idempotency, cancellation and duplicate delivery are tested.
- [ ] Provider health/fallback behavior is verified without implicit cloud routing.
- [ ] MCP integration covers auth, tool filtering, timeout, error, revocation and disconnect.
- [ ] Monitoring and alerts are exercised; logs are tested for secret redaction.
- [ ] Rollback procedure and recovery drill are documented.
- [ ] Human acceptance tests completed for every enabled interface/channel.
- [ ] Capability/readiness report matches actual behavior and verification evidence is recorded.

## Adversarial tests

1. Prompt injection in retrieved content cannot change permissions or policy.
2. Unknown tools, shell requests, oversized payloads, malformed model output and repeated calls are rejected.
3. Missing approver, approver exception, expired approval or action mismatch fails closed.
4. A future URL fetcher rejects loopback/private/link-local/metadata IPs, DNS rebinding, redirects to private networks, non-HTTPS schemes and oversized responses.
5. Logs redact API keys, OAuth tokens, cookies, message bodies and personal memory.
6. Concurrent memory writes and crashes do not silently corrupt or overwrite data.
7. Scheduled jobs resume without duplicate external side effects.
8. Untrusted or unsigned plugin code cannot auto-load.
9. Microphone permissions remain off unless explicitly enabled; stop control interrupts capture.
10. Backups restore into a clean workspace and pass integrity checks.

## Test tiers

- Tier 0 offline CI: unit tests, compile check, schemas, mocked providers, no network/real credentials.
- Tier 1 disposable integration: local model, temporary workspace, test MCP server, isolated container, synthetic account.
- Tier 2 hardware opt-in: microphone/speaker/wake-word on designated hardware with visible recording state and consent.
- Tier 3 staging: one configured channel, least-privilege OAuth, rate limits, rollback and restore drill.
- Production canary: only after mandatory gates pass; start with low-risk read-only workflows.

Never run real payments, external applications, customer messages, or destructive device actions in automated CI.
