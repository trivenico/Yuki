# Yuki production gap audit — 2026-11-11

**Audited repository:** `trivenico/Yuki`  
**Audited base:** `main` at `14772c850e9ef3936dc610ea570b568d67d7505a`  
**Method:** GitHub source/tree/documentation review and exact-head GitHub Actions check inspection. No local checkout, clean-install run, real-device test, penetration test, independent security review, or production deployment was performed. This is a gap inventory, not a production certification.

## Executive decision

**Yuki is not production-ready as a complete personal AI product.** It has substantial reusable foundations: authenticated conversation APIs, real provider adapters, a bounded agent loop, durable task and approval infrastructure, local memory, OAuth/MCP components, device pairing/inference, and income-opportunity tracking. The remaining work is primarily end-to-end integration, product surfaces, deployment/recovery, security assurance, and acceptance evidence—not simply adding more agent names.

Do not describe a subsystem as production-ready merely because its module, API, phase document, or unit tests exist. Track each capability separately as implemented, integrated, verified on target platforms, operationally deployable, and released.

## Snapshot and evidence

- The recursive `main` tree contained 631 entries, including 140 documentation files and 163 Python test files. These are inventory counts, not coverage or quality scores.
- There are 23 GitHub Actions workflow files. The main quality workflow currently runs Python 3.11 on Ubuntu, Windows, and macOS, with dependency installation, `pip check`, compilation, and unittest discovery. The dependency-manifest audit is separate. The presence of a workflow is not proof that every security, packaging, release, or operational gate exists.
- The repository contains no desktop-specific implementation, desktop framework project, or native desktop packaging manifest in the audited tree. Existing web chat and Textual terminal/dashboard code are not a desktop application.
- No `pyproject.toml`, `setup.py`, `setup.cfg`, or other Python package/console-entrypoint manifest was found in the audited tree. The CLI is source-run, not yet a versioned installable distribution.
- The phase ledger was last reconciled on 2026-11-02 and explicitly retains unknown historical phase serials and open blockers. It needs a current reconciliation before it can serve as a reliable release ledger.

## Product capability matrix

| Product area | What exists | What remains before production acceptance |
|---|---|---|
| AI conversation core | OpenAI-compatible, Ollama, Gemini CLI and hybrid provider adapters; provider factory; authenticated conversation API; bounded agent execution loop and structured tool protocol; streaming and cancellation foundations | One canonical application composition for CLI/web/desktop; provider-by-provider contract and live smoke tests; robust native tool-call interoperability; context budgeting; timeout/cancellation semantics; retry/idempotency; safe fallback; multi-turn tool-loop acceptance; explicit privacy-aware routing and user-visible provider/model controls |
| Agent orchestration / “100 agents” | Specialist coordinator, capability contracts, task journal, durable task engine, worker supervisors, guarded skills and approvals | Do not create 100 disconnected agent shells. Define a versioned specialist contract, registry, task ownership, capability grants, budget/resource policy, shared memory rules, tracing, handoff protocol, cancellation, recovery/fencing and eval suite. Add agents only where measurable evaluations show value |
| Companion | Persona, identity labels, explicit mood self-report, proactive/reminder/evolution and memory modules | Integrate companion state into the actual conversation lifecycle; durable scheduler and notification delivery; consented memory lifecycle/export/delete/retention; explainable personalization; cross-session acceptance tests; no biometric or ambient sensing without dedicated consent and platform permission |
| Assistant / tools | Guarded terminal runtime, skill registry, approval services, tool policy, MCP runtime foundations, web chat with deny-all tools by default | Unify tool policy and schemas across interfaces; production-grade execution isolation; approval receipts bound to exact action/target/arguments; cancellation and recovery; prompt-injection containment; safe browser interaction; end-to-end UI approval and audit views |
| Desktop app | No native desktop app implementation found | Choose and implement a supported desktop shell; signed installers and updates; OS keychain/credential storage; secure IPC; single-instance/lifecycle; background service controls; crash recovery; logs/diagnostics; accessibility; auto-update rollback; Windows/macOS/Linux install, upgrade and uninstall acceptance |
| CLI | `yuki_cli.py` provides doctor, provider-status, ask and simple chat; legacy guarded runtime remains | Package metadata and stable `yuki` entry point; config migration; secure login/token lifecycle; stable JSON output and exit codes; shell completion; scripting/streaming; command compatibility policy; installer and clean-machine upgrade/uninstall tests |
| Models and inference | Model/provider registries, Ollama and remote device inference, model discovery/evaluation modules, capability metadata and worker leases | Establish whether “Yuki models” means model routing/hosting or proprietary model training. No evidence of a trained proprietary foundation model. Add reproducible benchmark datasets, task-specific quality/safety evals, hardware profiling, model artifact provenance/license checks, quantization/upgrade rollback, resource admission, and provider fallback acceptance |
| Memory and knowledge | Local lexical memory, opt-in embedding retrieval, knowledge index/graph, conversation persistence and Postgres adapter | Unified user-facing inspect/export/delete/retention and backup lifecycle; schema-versioned migrations; corruption recovery; encryption/key rotation; restore drills; retention enforcement; retrieval relevance/privacy evaluations; clear local/cloud data boundaries |
| Device pairing and inference | Owner-bound pairing, device inventory/revocation, authenticated inference API, local worker and lease foundations | Device agent installation/update/attestation; heartbeats; network transport; reconnect/offline queue; request replay protection; per-command audit/idempotency; revocation races; multi-OS and real hardware tests |
| Local-network device control | Typed endpoint/capability registry and owner-bound pairing/inference foundations | Secure authenticated command transport and request signing; mDNS/DNS-SD discovery UX; installable device agents; explicit capability grants; per-command audit/idempotency; real-device/network-isolation tests |
| Online earning | Owner-scoped opportunity lifecycle, authenticated API, read-only discovery boundary, Upwork policy research | No live platform adapter or verified revenue path. Complete provider-specific OAuth/approval and terms/region checks; implement official API/MCP adapters only; owner selects opportunities; draft-first proposals/messages; approval for submission/spend; reconcile actual platform earnings with evidence; never promise earnings |
| OAuth / MCP / integrations | PKCE, OAuth/OIDC validation, encrypted credential vault primitives, provider registry, MCP transport/runtime and policy foundations | Reconcile open draft PRs (#701, #694, #678, #666 at audit snapshot); complete lifecycle end-to-end per provider; token revocation/rotation and OS-key storage acceptance; tool inventory review; exact schema pinning; ambiguous-write reconciliation; connector-specific integration tests and owner UX |
| Defensive security tools | Explicit scope, approvals, sandbox/recon/IDS foundations; dry-run firewall | No live firewall mutation until durable expiry/rollback/restart recovery and privileged OS acceptance; strengthen isolation and output bounds; test DNS/redirect/private-address paths; separate security lab from general assistant permissions; independent security review |
| Deployment / operations | ASGI runtime, local launcher, Dockerfile, basic diagnostics and CI | Supported deployment profile; TLS/reverse-proxy hardening; trusted hosts/CSRF/rate limiting; secret provisioning/rotation; process supervision; health/readiness/liveness; metrics/traces/redacted logs; migrations; backup encryption and restore; disaster recovery; capacity/load testing; release/rollback runbooks |
| Packaging / updates | Windows batch and Unix shell installers/launchers; portable path foundations | Reproducible versioned artifacts; dependency lock and SBOM; signatures/checksums; supported Python/OS matrix; clean install/upgrade/uninstall; offline/portable mode; update verification and rollback; release automation |
| Quality / governance | Broad unittest suite, CI matrix, many phase documents and a phase ledger | Coverage measurement; static analysis/type checks and policy gates; full dependency/security/secret/license scans; end-to-end acceptance suite; flaky-test policy; test artifacts; independent review; reconcile stale docs, phase serials, open draft PRs and duplicated historical work |

## High-priority release blockers

### P0 — Security and data protection

1. Verify repository visibility and inspect the complete Git history for secrets/private data; rotate any exposed credentials. The prior personal-use audit reported the repository as public, but visibility was not re-verified as part of this source-tree pass.
2. Define and test an encrypted backup/restore path. The prior production baseline did not find a verified encrypted backup implementation or restore proof. A backup script or encrypted drive recommendation is not application-level encrypted backup.
3. Establish one threat model covering local files, browser/API sessions, OAuth credentials, MCP servers, paired devices, local network control, agent workers and model-supplied tool requests.
4. Ensure every consequential action is authorized immediately before dispatch and bound to actor, exact target, arguments, expiry and idempotency key. Owner authentication alone is not equivalent to informed confirmation for a message, purchase, proposal, device action or deployment.
5. Complete security review of worker recovery, credential revocation, execution fencing, OAuth token lifecycle and cross-device revocation races.

### P1 — Product completeness

6. Deliver a shared application service layer consumed by CLI, web and desktop rather than separate partial runtimes.
7. Integrate companion, memory, voice and durable scheduling into that service with explicit consent and reliable lifecycle handling.
8. Build the native desktop application and package the CLI; neither can be inferred from a web UI or source launcher.
9. Complete authenticated real-device command transport before claiming LAN device control.
10. Build at least one compliant, live earning integration end-to-end before describing online earning as operational.

### P2 — Operational maturity

11. Add reproducible packaging, SBOM, signed artifacts, release automation, rollback and supported-version policy.
12. Add metrics/tracing, privacy-safe diagnostics, health probes, resource admission, load tests and incident/recovery runbooks.
13. Measure coverage and add static/type/security checks without weakening existing quality gates to manufacture green CI.
14. Reconcile the phase ledger and open draft PR backlog; close, supersede or rebase stale branches only after source-level comparison. Do not bulk-merge old drafts.

## Concrete inconsistencies to resolve

- `ui/README.md` says a graphical dashboard and Textual TUI are not implemented, while the repository contains `ui/dashboard.py`, task controls, and web chat assets. Rewrite it to describe the actual interfaces and their limits.
- `README.md`, `docs/INSTALLATION.md`, the older personal-use audit, and phase documents describe different generations of the provider/runtime architecture. Make one capability matrix authoritative and link all other docs to it.
- The code tree has a real web chat/API runtime and provider tool loop, but the web launcher intentionally injects a deny-all tool policy. The CLI's simple chat also explicitly disables tool execution. Therefore “agent loop exists” must not be conflated with “all product interfaces can safely execute agent tools.”
- The phase ledger is stale relative to phases 162–201 and does not yet reconcile all recent merges, draft PRs and undocumented serials.
- The root README's target `src/yuki/` layout is explicitly aspirational; the codebase still uses root-level modules. Do not perform a bulk migration before packaging/import-compatibility tests exist.

## Recommended execution order

2. **Create a single product capability/readiness contract:** implemented/configured/available/tested/deployed/released states, consumed by doctor, CLI, web and future desktop.
3. **Consolidate the shared application runtime:** one provider, conversation, memory, tool policy, approvals, task, event and lifecycle composition.
4. **Complete the security/recovery baseline:** threat model, data inventory, encrypted backup/restore, migrations, worker fencing, credential revocation, audit and recovery tests.
5. **Ship CLI packaging and native desktop shell:** signed artifacts, updates, diagnostics, accessibility and clean install/upgrade/uninstall tests.
6. **Complete companion and voice end-to-end:** persistent reminders, memory controls, STT → conversation → TTS, interruption, permissions and device matrix.
7. **Complete local-network device agents:** pairing/discovery, authenticated command transport, capability grants, audit and real-device security acceptance.
8. **Activate compliant income integrations:** begin with one officially supported platform, read-only first, owner-directed choices, draft-first actions and actual earnings reconciliation.
9. **Scale specialist agents only after evaluations:** add agents based on benchmarked task quality, reliability, latency, privacy and cost—not a target count.
10. **Release only after operational gates:** signed release artifacts, migration/backup/restore, monitoring, rollback, security review and supported-platform acceptance.

## Exit criteria for calling Yuki production-ready

A release candidate must have all of the following evidence on the same tagged build:
- Clean install, upgrade, uninstall and rollback on each supported OS.
- Real provider round-trip and task/tool workflows tested with explicit approvals.
- Conversation, companion, memory, voice and task state survive restart as specified.
- No unauthorized tool/device/network/financial action in the security regression suite.
- Credential storage, rotation, revocation, encrypted backup and restore verified.
- Desktop app and CLI installable artifacts signed and reproducible.
- Local device control tested on real enrolled devices, including revocation and authorization races, before it is advertised as supported.
- Observability, incident response, data deletion and recovery runbooks exercised.
- All required CI/security gates green on the exact release commit; unresolved P0s closed; independent security review recorded.
- Documentation and UI accurately state which integrations and capabilities are actually enabled.

**No delivery date is asserted by this audit.** The remaining schedule depends on the supported operating systems, deployment model, hardware, provider choice, and acceptance results.
