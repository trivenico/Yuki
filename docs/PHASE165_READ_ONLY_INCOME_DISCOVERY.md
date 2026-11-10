# Phase 165 — Read-only income opportunity discovery boundary

## Objective

Introduce a fail-closed execution boundary between the reviewed platform capability registry and read-only opportunity discovery adapters. This is infrastructure for future verified platform integrations; it does not ship a live marketplace connector.

## Contract

- Only a platform present in the registry, enabled, within its policy review window, and explicitly declaring `read_opportunities` as `official_api` may execute.
- Only an adapter explicitly injected for that platform ID may run. Unknown platforms and absent adapters fail closed.
- Discovery requests bound query length, region syntax, and result count; regions must be listed by the platform.
- Adapter inputs contain query, region, and limit only. Credentials must remain in the adapter's approved secret/transport layer and must never be passed through this service.
- Candidate source URLs must be HTTPS, credential-free, use the platform's canonical domain or a subdomain, and avoid nonstandard ports.
- Candidate metadata and timestamps are validated; duplicate external IDs are removed; adapter errors are replaced with a stable non-sensitive error.
- Results include platform ID, adapter version, region, candidates, and retrieval timestamp for provenance.

## Explicit non-goals

- No generic web scraper, browser automation, login automation, CAPTCHA/MFA bypass, or anti-bot evasion.
- No default platform is enabled and no real platform adapter is bundled.
- No opportunity is automatically persisted as verified, and no estimated value is treated as income.
- No applications, proposals, listings, messages, contracts, purchases, payouts, or withdrawals.
- No credentials, cookies, OAuth tokens, or provider response headers enter LLM context or logs.

## Acceptance

Tests cover current/stale/absent policy, disabled/unknown platforms, official API mode, region enforcement, bounds, canonical-domain validation, duplicate handling, malformed adapter output, and secret-safe adapter failures.
