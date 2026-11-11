# Spatial source contracts

## Scope

Yuki's first spatial-data increment is a renderer-independent metadata contract and in-memory registry. It does not fetch external data, render a map, perform network discovery, track people, or control devices. Existing paired-device identity and authorization remain separate from spatial source registration.

## Source contract

Each source declares a stable ID, provider/version, domain/schema version, license and attribution, commercial-use and redistribution status, data sensitivity, optional fixed HTTPS origin, and bounded response/entity/timeout/refresh/staleness budgets. Unknown licensing is explicit and must never be treated as permission. Sources are disabled by default; enabling a source is not proof that provider terms or endpoints have been approved.

The endpoint-origin check is metadata validation only, not a fetcher or SSRF defense. Future adapters must independently enforce fixed-origin requests, redirect restrictions, DNS/IP resolution policy, response/entity/concurrency/time budgets, secret isolation, and safe error/log redaction. They must not accept model-supplied arbitrary URLs.

## Record contract

Records carry a stable provider record ID, observed and fetched timestamps, optional publication time, freshness state, optional confidence, and an explicit estimated-data flag. Timestamps must be timezone-aware and cannot be later than fetch time. Estimated records cannot be labelled current. Freshness is supplied by a future source adapter using its source-specific policy; this package does not infer real-time status.

## Privacy and source governance

- Public, owner-private, and restricted sensitivity are distinct values.
- Device overlays must use enrolled owner devices and their existing authorization; this registry grants no device-control authority.
- Do not add face recognition, ALPR, people search, individual tracking, or covert surveillance layers.
- Verify provider terms, attribution, commercial use, redistribution, retention, and rate limits before enabling an adapter.
- Public code licenses do not grant rights to third-party datasets, imagery, models, or camera feeds.
- No microphone/camera activation or calling features are introduced; speech remains a separate opt-in capability.

## Next integration gates

1. Add source-specific adapter interfaces with fixed provider origins and transport-level SSRF protections.
2. Add typed scene actions with strict schemas, capability checks, risk classification, owner approval where required, cancellation, and audit events.
3. Add bounded versioned scene snapshots with no credentials or private device identifiers.
4. Add source health/freshness metrics and replay tests before connecting a renderer.
5. Evaluate a renderer behind an optional interface so CLI/headless operation remains available.

No live source adapter or map UI is claimed by this increment.
