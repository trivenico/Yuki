# God's Eye View Architecture Review — Yuki Adaptation

**Reviewed:** 2026-11-11  
**Upstream:** https://github.com/bilawalsidhu/gods-eye-view  
**Decision:** Adopt selected architectural patterns; do not copy the application wholesale or treat its demo/security posture as production-ready.

## Executive summary

God's Eye View is a local-first, browser-based geospatial situational-awareness client. It combines a 3D Cesium globe, modular public-data layers, source attribution, server-side provider proxies, voice-operated scene controls, and shareable/replayable scenes. Its README explicitly describes the project as an evolving exploration/demo foundation rather than a hardened production service. Its data and asset licenses remain independent of the MIT source-code license.

The strongest lessons for Yuki are architectural:
1. Separate data acquisition, normalization, domain records, rendering, and interaction.
2. Make every source an independently governed adapter with freshness, provenance, attribution, licensing, quotas, and failure state.
3. Keep secret-bearing provider calls behind fixed-origin server adapters; never expose an arbitrary URL fetch primitive to a model or browser.
4. Give voice/agent control a small typed action interface that manipulates an application session, not direct access to rendering internals or arbitrary code.
5. Treat map scenes and replay as versioned, bounded, importable data—not executable scripts.
6. Make local-only networking and explicit sharing boundaries the default.

## What the upstream project demonstrates

### Product and interaction

- A 3D globe with aircraft, vessels, satellites, earthquakes, fires, weather, transit, infrastructure, and public camera layers.
- Trackable contacts, nearby-contact rosters, trails, metadata, cockpit camera modes, and camera handoffs.
- Voice whiteboard annotations and voice-operated navigation/filter/selection.
- Sensor-style rendering, detection overlays, HUD, scene director, and serializable share links.
- Historical/replay-oriented scenes and source attribution.

These features show how a spatial workspace can turn many independent feeds into one navigable context. They are not evidence that every feed is authoritative or suitable for operational decisions.

### Architecture and code organization

The upstream contribution guide describes:
- Application assembly separated from portable layer/source modules.
- UI controllers separated from source factories and application services.
- Sources acquire records; renderers own Cesium resources.
- Shared voice controls consume a session interface while protocol adapters own provider-specific connection details.
- Framework boundaries are enforced with import-boundary checks.

This is a useful separation of concerns for Yuki's future map/desktop experience. Yuki should use its existing Python service, capability, device, task and policy boundaries rather than introducing a second agent runtime.

### Security and operations

The upstream security guide provides concrete defenses:
- Secret-bearing API keys stay server-side; browser receives only deliberately public/restricted keys or short-lived ephemeral tokens.
- Provider proxies target fixed upstream hosts; arbitrary-URL fetches are avoided to reduce SSRF.
- Redirects, upstream origins, body sizes, timeouts, session counts, and request rates are bounded.
- Host/origin checks, CSP, sanitized errors, and redacted logs are applied.
- The app defaults to loopback; LAN exposure is an explicit opt-in with warnings and provider-side spend controls.
- Feed-sourced names/callsigns are untrusted input, even after normalization; the voice agent receives only a fixed app-control tool surface.

The project itself cautions that the dev/preview server is not a hardened production service. Yuki must not copy its dev-server deployment model as a production hosting pattern.

## Yuki's current baseline

The Yuki repository already contains foundations that should be reused:
- Owner-bound device pairing and revocable device credentials: `core/device_pairing.py`.
- Durable paired-device inference jobs with lease fencing: `core/inference_jobs.py`.
- Authenticated device/inference API: `api/inference.py`.
- Local-network device-control registration and typed capability concepts: `core/lan_devices.py` (see current tree and its tests).
- Durable task state, approvals, worker leases, specialist orchestration and capability manifests.
- Provider/model capability registry and local inference worker paths.
- Evidence-backed capability status rather than declaring a feature complete because a module exists.

The current local-device/inference APIs are control-plane foundations, not a complete fleet product. Real device agents, cross-platform installation/update, authenticated command transport, heartbeats, offline queue/reconnect, device attestation, real hardware tests, and robust recovery remain required.

The existing Phase 200 local-network-device-control document contains historic call-related capability names and descriptions. Calling was subsequently removed by PR #715 at the user's explicit request. Do not reintroduce call features; update stale docs/contracts when this area is next touched. Speech/voice assistant features are separate and remain in scope.

## Recommended Yuki architecture

### 1. Spatial data source contract

Add a typed, versioned source contract before adding live feeds:

- `source_id`, provider, source version and schema version.
- Data class and supported geographic/time bounds.
- Canonical record ID and stable deduplication key.
- Observed time, provider publication time, fetched time and cache age.
- Freshness state: current, stale, unavailable, estimated, replayed, or unknown.
- Provenance URL/reference, attribution text, license identifier, commercial-use eligibility and redistribution constraints.
- Confidence/precision metadata and explicit estimate-vs-observation flag.
- Rate-limit budget, timeout, maximum response bytes/entities, retry policy and cache policy.
- Data sensitivity, retention and user consent policy.
- Health state and last successful/failed fetch without leaking provider secrets.

Provider adapters return validated domain records. They do not create UI objects, issue arbitrary network requests, or grant agents new permissions.

### 2. Layer and renderer separation

Keep a renderer-agnostic domain model:
- Sources acquire data.
- Normalizers validate units, coordinates, timestamps and identifiers.
- Domain services own filtering, joins, deduplication and temporal logic.
- Renderers subscribe to domain records and own their GPU/map resources.
- UI state is a projection of application state; UI components do not own ingestion or policy.

For a future 3D map, evaluate CesiumJS (or an equivalent maintained geospatial renderer) behind a narrow `SpatialRenderer` interface. Keep map rendering optional and isolated from the core assistant so CLI and headless Yuki remain fully usable.

### 3. Agent control through typed scene actions

Expose a small action vocabulary such as:
- `scene.navigate`
- `scene.set_layers`
- `scene.select_record`
- `scene.filter_records`
- `scene.annotate`
- `scene.reset_view`
- `scene.save_snapshot`
- `scene.replay_snapshot`

Every action must have a schema, risk classification, owner/capability check, bounded arguments, audit event, cancellation semantics and deterministic result. Read-only scene queries should be separate from mutation actions. Never let a model supply JavaScript, arbitrary Cesium commands, SQL, or arbitrary URLs.

### 4. Versioned scenes and replay

Store scene snapshots as bounded JSON with:
- schema version, created-at and source snapshot times;
- camera position/orientation and selected basemap;
- enabled layer IDs and validated layer parameters;
- selected record IDs (not live bearer links);
- annotation geometry and author;
- source references, attribution and freshness state.

Import must validate size, nesting, coordinate ranges, layer allowlists, URL-free source references, and schema migrations. A share link should serialize only an allowlisted snapshot and must not contain credentials, private device identifiers, personal data or arbitrary executable content. Replay must clearly label historical, simulated and estimated records.

### 5. Security model

- Loopback-only by default. LAN access requires explicit owner opt-in and authenticated transport.
- Device control remains separate from public-data visualization. A map selection must never imply authority to command a device.
- Provider adapters use fixed origins and paths; redirect and DNS resolution policy is validated at connection time.
- Apply request, response, entity-count, concurrency, cache and cost budgets.
- Keep provider credentials in an OS credential store or server-side secret broker; only intentionally public, provider-restricted tokens reach the client.
- Treat every feed field, camera caption, callsign, location label, imported scene and retrieved description as untrusted prompt-injection input.
- Voice/agent actions call the same typed application service as UI actions and cannot bypass policy or approvals.
- No people-search, face recognition, individual tracking, license-plate lookup, or covert surveillance features. Avoid copying upstream ALPR-camera location layer. Limit public-data visualization to aggregate or non-personal situational context and legitimate user-owned devices.
- No silent mic/camera capture. No calling features.

### 6. Reliability and observability

For each source/layer, report:
- last successful fetch and age of displayed data;
- fetch latency, cache hit ratio and error category;
- upstream quota/rate-limit state;
- records accepted/rejected and schema validation failures;
- stale fallback activation and data-loss interval.

Use bounded structured logs and metrics, redact tokens and sensitive coordinates where applicable, pause background polling when the app is inactive, coalesce duplicate requests, and prevent overlapping refreshes. Do not equate a successful HTTP response with a healthy or complete dataset.

### 7. Source governance and licensing

The upstream repository makes clear that MIT source licensing does not grant rights to third-party datasets, models, map tiles or imagery. Yuki needs a source registry and release gate that rejects a source without:
- documented official endpoint and terms;
- commercial-use status and attribution obligations;
- retention/redistribution rules;
- rate-limit and caching policy;
- privacy/sensitivity assessment;
- a tested fallback and stale-data behavior.

Do not copy upstream data bundles, model assets, camera catalogs, or derived datasets into Yuki without separately verifying each license and permitted use. The upstream's own data-source documentation is a useful pattern, not a license grant to Yuki.

## Prioritized implementation plan

| Priority | Deliverable | Acceptance criteria |
|---|---|---|
| P0 | Spatial source contract and registry | Schema/version, provenance, timestamps, freshness, license, quotas and health validated by tests |
| P0 | Typed scene action service | Read/write actions are allowlisted, policy-checked, audited, cancellable and provider-independent |
| P0 | Scene snapshot schema | Strict bounded JSON, migrations, safe import/export, no secrets or executable payloads |
| P0 | Source security harness | SSRF/redirect/DNS rebinding tests, body/time/entity limits, prompt-injection fixtures |
| P1 | Read-only spatial data adapters | Start with low-risk authoritative public data; official sources only; no personal tracking |
| P1 | Spatial renderer spike | Isolated renderer interface, performance baseline, keyboard/accessibility and headless fallback |
| P1 | Timeline/replay service | Explicit observed/estimated/replayed labels, deterministic snapshots and provenance |
| P2 | Agent-assisted analyst workspace | Typed natural-language filters/summaries with citations and visible stale/uncertain state |
| P2 | Optional local-network device overlay | Only enrolled devices, owner-approved status/capabilities, revocation and audit; no implicit command authority |
| P3 | Desktop integration | Native lifecycle, secure credential store, signed updates, crash recovery and cross-platform tests |

## What not to copy

- Do not copy the entire Cesium application into the assistant core.
- Do not assume the upstream dev server is safe for internet exposure.
- Do not bundle datasets or 3D assets based only on the upstream repository's MIT code license.
- Do not expose arbitrary URL fetch, arbitrary scene scripts or unrestricted voice tools.
- Do not copy ALPR, individual tracking, face recognition or surveillance functionality.
- Do not add a second task queue, agent runtime, device registry or memory system where Yuki already has one.
- Do not claim production readiness based on the upstream demo or on a passing isolated unit test.

## References reviewed

- Repository README: https://github.com/bilawalsidhu/gods-eye-view
- Architecture/contribution guide: https://github.com/bilawalsidhu/gods-eye-view/blob/main/CONTRIBUTING.md
- Security model: https://github.com/bilawalsidhu/gods-eye-view/blob/main/SECURITY.md
- Data sources and attribution: https://github.com/bilawalsidhu/gods-eye-view/blob/main/DATA_SOURCES.md
- License and third-party carve-outs: https://github.com/bilawalsidhu/gods-eye-view/blob/main/LICENSE
- Current-state engineering notes: https://github.com/bilawalsidhu/gods-eye-view/blob/main/docs/CURRENT-STATE.md

This review is based on repository documentation and code-organization guidance. It is not an independent penetration test, performance benchmark, license opinion, or validation of live provider availability.
