# Upstream capability audit for Yuki

Audit date: 2026-10-17. This is an architecture and feature review of the public repositories below, not a claim that their entire codebases were imported or independently security-audited.

## Upstream projects reviewed

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — README, project root and LICENSE. The repository describes persistent memory/procedural skills, model/provider configuration, messaging gateways, MCP, cron, context files, toolsets and interruptible CLI work. Its LICENSE is MIT.
- [AutoClaw](https://github.com/tsingliuwin/autoclaw) — README, safety notice, root and LICENSE. It emphasizes headless/container automation, portable skill packages, recipes/batch execution, OpenAI-compatible endpoints and browser tooling. Its LICENSE is MIT. Its own safety notice explicitly warns that unattended model-generated shell execution can damage systems or disclose credentials; Yuki should not copy its unattended-execution defaults.
- [OpenClaw](https://github.com/openclaw/openclaw) — README, root and LICENSE. It emphasizes cross-device assistant operation, messaging channels, provider configuration, extensions/skills, gateway and device nodes. Its LICENSE is MIT.
- [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) — README, root and LICENSE. It emphasizes personal-device/local AI, selectable agents, skill packages, deep research, scheduled monitoring, model/runtime evaluation and deployment presets. Its LICENSE is Apache-2.0.

## Feature decisions for Yuki

| Pattern | Existing Yuki baseline | Recommended next action |
|---|---|---|
| Provider abstraction and local/offline model | Provider scaffold/config exists; no production LLM tool loop | Build a tested provider contract and explicit capability/readiness report before adding autonomy |
| Procedural skills | Skill registry and approval gate exist | Define a versioned portable skill manifest; reject unknown permissions and dependencies by default |
| Persistent memory and learning | Local notes and opt-in evolution review exist | Add export/import, schema versions, retention controls and backup/restore tests |
| Interruptible interaction | Chunked TTS interruption is partial | Add audio VAD integration and real hardware tests; do not advertise full barge-in yet |
| Messaging and device gateways | Not fully implemented | Add one channel at a time behind opt-in pairing, least-privilege scopes, rate limits and revocation |
| MCP | Restricted client primitive, not fully runtime-integrated | Add runtime discovery/status, explicit tool approval, audit trail and connection timeouts |
| Browser automation | Allow-listed read-only page reading | Keep browser in a disposable profile; later write actions must be separately permissioned |
| Headless/portable deployment | No complete portable distribution yet | Add root/path selection, environment checks, backup/restore, and Windows/Linux launch wrappers |
| Scheduled monitoring | Local daily evolution scheduler is process-bound | Add OS scheduler integration only after restart recovery, idempotency and opt-in consent |
| Security controls | Approval-gated skills and prompt scanning exist | Keep model-generated shell disabled by default; isolate risky jobs and test prompt injection adversarially |
| Evaluation/observability | CI tests exist, some integrations remain unverified | Add offline test fixtures, capability matrix, logs with secret redaction and reproducible integration checks |

## Import and licensing policy

Do not wholesale-copy files simply because a feature looks useful. Reimplement the patterns using Yuki's interfaces and tests where practical. Before copying any specific upstream file, inspect that file's license headers, dependencies, transitive licenses, and required attribution. Preserve notices for any copied/modified MIT files and satisfy Apache-2.0 notice and change-marking requirements when applicable. A repository-level license does not automatically clear third-party assets, bundled skills, model weights, or dependencies.

## Phase exit criteria

- No new capability is labelled supported solely because a module exists.
- Optional dependencies fail closed with actionable errors.
- Secrets, health data, tokens, browser profiles and personal memory are never committed.
- Tool execution remains approval-gated and bounded.
- Windows and Linux path/launch tests run in CI before claiming portable support.

## Competitive positioning and 90-day gap plan

This is a feature/architecture comparison, not a benchmark of every upstream commit. Competitor capabilities change quickly; verify current upstream docs before copying patterns.

| Project | Publicly emphasized strengths | Yuki's present gap | Recommended response |
|---|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Durable self-improving skills/memory, multi-provider CLI, messaging gateway, cron, MCP, interruptible work and setup/diagnostic UX | Yuki's evolution cycle proposes reviewed changes but does not learn reusable skills from task outcomes; no production multi-channel gateway or durable job runner | Build a reviewed skill-improvement loop, durable jobs, and one reliable channel adapter before expanding channels |
| [OpenClaw](https://github.com/openclaw/openclaw) | Gateway/control plane, broad messaging and device channels, native apps, extensions, onboarding and operational documentation | Yuki remains primarily a Python terminal application; no mature gateway, device-pairing lifecycle, broad channel coverage, or polished install wizard | Design a local control-plane API, authenticated pairing, session management, revocation, and a minimal web UI; add channels incrementally |
| Moltbot | Historical name in the OpenClaw lineage; treat OpenClaw as the primary current comparison, not a separate independent architecture | Risk of duplicating the same competitor in feature counts | Avoid duplicate scoring; track project identity and redirects in benchmark notes |
| [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) | Local-first personal AI stack, explicit agent/runtime primitives, evaluation and optimization across accuracy, energy, latency, and cost; skill interoperability | Yuki's local provider and token accounting are only partly integrated; no reproducible quality/cost/latency benchmark suite | Create task benchmark fixtures and report quality, latency, token/cost estimates, memory use, and local/cloud data routing |
| [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) | Compact Rust runtime, broad provider/channel/tool coverage, service operation, hardware integrations and deliberate deployment story | Python dependency footprint and optional integration setup are not packaged into a polished installer/service | Keep Python for now; add supported platform matrix, one-command diagnostics, service lifecycle and reproducible packaging before considering a rewrite |
| [IronClaw](https://github.com/nearai/ironclaw) | Security-focused agent runtime patterns around isolation, constrained tool execution and secret handling | Yuki's skill/plugin imports execute with process privileges; Docker sandbox is opt-in and only protects supported paths | Treat plugin isolation as a release blocker for untrusted extensions; use isolated worker processes/containers and narrow capability tokens |

### Product principle: compete on trust and completion, not raw feature count

Yuki should aim to be the assistant developers and individuals can trust with local files and security workflows. It should not claim to be “number one” until public, reproducible benchmarks demonstrate that claim. A safer differentiator is local-first by default, transparent about capability, human approval for consequential actions, and measurable end-to-end task completion.

### Prioritized gaps

**P0 — correctness and release gates**
1. Keep CI green. Current PR workflow run must pass all unit tests; fix existing registry/provider failures rather than bypassing tests.
2. Add contract tests for every provider, registered skill, approval path and optional dependency. A registered skill must not be described as connected if it is a scaffold.
3. Add dependency and secret scanning, supported Python/OS matrix, build/package smoke tests, and an explicit release checklist.
4. Publish a capability matrix (working, experimental, scaffold, disabled) generated from tested integration status.

**P1 — security posture**
1. Threat-model trust boundaries: prompts and retrieved documents are untrusted; tools and plugins are privileged.
2. Isolate untrusted skills in separate processes/containers with explicit filesystem/network/resource permissions; never enable wildcard plugin trust by default.
3. Implement durable, expiring approval grants bound to exact action, arguments, target, session and expiry; maintain a redacted audit log.
4. Add prompt-injection, path traversal, SSRF, secret-leak, replay, malicious-plugin, and approval-bypass adversarial tests.
5. Add signed release artifacts, dependency pinning/SBOM, vulnerability reporting, and key rotation/revocation for agent identity.

**P2 — daily usefulness**
1. Build a durable scheduler/job queue with idempotency, cancellation, restart recovery, retries, and user-visible job history.
2. Ship a local control plane and one high-value channel; require pairing, scoped permissions, rate limits, and revocation.
3. Make provider calls observable: OpenTelemetry traces, token/cost accounting, timeout/retry policy, latency and error metrics.
4. Upgrade memory with source/provenance, per-item retention/deletion, export/import, backup/restore, and optional semantic retrieval only when dependencies are configured.
5. Add least-privilege OAuth integrations for calendar, email drafts, tasks, files and issue trackers; read-only/draft-first by default.

**P3 — developer adoption**
1. Create a doctor diagnostic, setup wizard, cross-platform packaging, clean uninstall, backup/restore and migration guide.
2. Define a versioned skill manifest with schemas, declared permissions, dependency metadata, tests and compatibility versions; use an interoperability standard where feasible.
3. Publish architecture/ADR docs, threat model, contributor guide, plugin author guide, API contracts and a supported capability matrix.
4. Run public benchmark suites for coding tasks, local-first privacy, security regressions, latency, resource use and end-to-end task success. Report methodology and failures, not just a single score.

### Acceptance gates for a credible “top choice” claim

- CI is green on every supported OS/Python target, with coverage for safety-critical modules.
- No critical/high unresolved security findings in an independent review.
- An untrusted plugin cannot read arbitrary host files, access credentials, or make network calls outside its declared grants.
- Approval bypass, prompt injection, secret leakage, and path/SSRF tests pass in CI.
- At least 30 representative end-to-end daily tasks have reproducible pass/fail fixtures and human-reviewed quality scoring.
- Installation, upgrade, rollback, export, restore, and uninstall are tested on clean machines.
- Benchmark results include baselines against named competitors and publish scripts, environment, and limitations.
