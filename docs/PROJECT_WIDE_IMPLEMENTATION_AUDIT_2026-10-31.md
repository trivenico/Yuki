# Yuki project-wide implementation and release audit

Audit date: 2026-10-31  
Audited default ref: `main` at `9ac32448c3d860265bf46214ced6f05c894c6ad4`  
Scope: recursive repository tree, README, roadmap, installation and production-readiness documentation, third-party notices, open PRs/issues, selected integration branches and workflow metadata.  
Status: engineering inventory, not a security certification or proof that all tests pass.

## Executive assessment

Yuki has substantial foundations for a local-first personal AI and agent platform: Python CLI, provider adapters, skill registry and approval boundaries, durable workflow/task infrastructure, specialist-worker components, local memory/knowledge features, optional voice and defensive tooling. The default branch explicitly states that Yuki is not production-ready.

The most important remaining work is integration and release discipline: make one supported application runtime, finish the user-facing desktop experience, consolidate provider/model selection, finish the Windows-to-OCI synchronization path, deploy and secure the cloud service, and prove supported install/use paths on clean Windows and Linux machines.

A module existing in the repository is not evidence that it is reachable from the supported CLI/desktop, configured, available, tested, or production safe.

## Repository snapshot

- Default branch: 414 tracked files; 98 files under `tests/`; 74 docs; 55 skill files; 40 core files.
- The default branch is not the same as the active feature branches. In particular, PR #400 and PR #399 are draft branches; their work must not be represented as already released on `main`.
- The default-branch README says the target `src/yuki/` layout is a migration target, not the current structure. Numerous legacy root-level Python modules remain.
- The repository is Python-centric. Windows and Linux launch scripts and a Dockerfile exist, but those are not a signed Windows installer or a deployed OCI service.
- No repository-wide local test run, clean Windows/Linux install, live inference, penetration test, or OCI deployment was performed as part of this audit.

## Capability inventory

| Area | Current state on main | Remaining work |
|---|---|---|
| CLI/runtime | Python CLI and command-driven runtime exist | One composition root, stable commands, clean-install verification, reduce root-level legacy coupling |
| AI conversation | Provider and bounded model-guided task foundations exist | Production native tool-call/ReAct loop, streaming, cancellation, context management, result verification and multi-turn acceptance tests |
| Model providers | Provider adapters/configuration and discovery exist | Consolidate provider catalog/selection branches; verify each provider against live APIs; capability matrix; robust auth/quota handling |
| Local inference | Ollama path and provider foundations exist; Colibri is in draft PR #399 | Windows/Linux real-model smoke tests, normal-PC hardware profiles, model download/storage management, offline recovery |
| OCI Cloud AI | OCI provider/router work is described in draft PR #395 | Actual tenancy configuration, private gateway, auth/IAM/Vault, deployment, network policy, budgets/alerts, monitoring and operational runbooks |
| Specialist agents | Durable coordinator/worker and registry foundations exist; PR #395 adds 20 starter profiles | Merge/rebase and wire to default runtime, define curated agent catalog, permissions, budgets, quality evals and end-to-end delegation; 100 agents are not yet delivered |
| Skills | Registry, approval gates, portable Markdown skill import foundations exist | Repository install/update lifecycle, dependency/resource handling, provenance, revocation, CLI/desktop parity and adversarial package tests |
| Desktop | Main contains UI foundations but no root `yuki_desktop.py`; packaging/first-run work is in PR #364 and sync desktop is in PR #400 | Choose one desktop shell and shared runtime bridge, merge compatible work, model/settings UX, accessibility, tray/notifications only if needed, Windows acceptance |
| Windows–Oracle sync | Not on main; PR #400 is draft | Pairing, least-privilege device scopes, bidirectional sync, all collection conflict policies, device management, reconnect/retry, revocation, encrypted backup/restore and two-device tests |
| Memory/knowledge | Local memory, lexical retrieval, knowledge indexer/graph exist | Unified data model, embeddings/vector retrieval if justified, inspection/export/delete/retention, migration, backup/restore, multi-device privacy policy and retrieval evaluation |
| Voice/companion | Optional STT/TTS/wake-word helpers and companion modules exist | End-to-end conversational voice, VAD/barge-in, tested wake-word model, OS permissions, background service lifecycle, device testing; no speaker biometrics without explicit consent design |
| MCP/integrations | Restricted MCP client primitive exists | Runtime/CLI/desktop integration, stdio server lifecycle, SSE where required, server trust UX, per-tool consent, audit and integration tests |
| Browser/automation | Allow-listed read-only browser/page reading foundations | Isolated profiles, prompt-injection adversarial tests, action-level permissioning, downloads/forms only after review |
| Security/defensive tools | Approval and audit foundations, passive IDS, dry-run firewall and isolated runner components | Independent security review, threat model, firewall expiry/recovery, isolation acceptance, authorization scope proof and incident response |
| Self-improvement | Proposal/staging/evaluation pipeline foundations exist in branches | Verify current-main integration, independent approval, isolated execution evidence, rollback, no autonomous promotion |
| Observability | Workflow/provider diagnostics and token estimate helpers exist | Real provider usage/cost metrics, structured redacted logs, traces, health endpoints, alerts, SLOs and dashboards |
| Packaging/operations | Install scripts, Dockerfile and packaging work in PR #364 | Signed Windows installer, Linux packages/service, upgrades/uninstall, data migration, auto-update policy, SBOM, release artifacts and restore drills |

## Known explicit gaps documented by the repository

The README and capability documents explicitly say these are absent, partial or unwired:

- No production-grade native tool-calling/ReAct runtime.
- No autonomous project builder with safe edit/test/commit workflow.
- No always-on wake-word listener; wake-word support is finite and model-dependent.
- No tested custom wake-word model.
- No production firewall rule expiry/recovery; live firewall mutation remains disabled/dry-run.
- No independent security review or security certification.
- MCP client primitive is not fully wired to runtime command/registry/UI and does not launch stdio servers.
- Token usage helpers are not yet connected to actual provider usage metadata.
- Vector-memory module is lexical retrieval, not an embedding vector database.
- Knowledge graph supports explicit CRUD/export; no automatic dream cycle.
- Voice does not yet provide end-to-end same-language conversational response and automatic VAD barge-in.
- Speaker verification is a fail-closed placeholder.
- Course-coach and several marketing/project skills are scaffolds, not complete connected services.
- Gmail integration is read-only; sending email is not implemented.
- Video generation wrappers create outlines; media rendering/TTS/upload are not implemented.
- External SSD portability is partial and still requires host Python/optional dependencies.

## Draft and overlapping pull requests

Open PRs observed during this audit include:

- #400 — Windows/Oracle sync contract foundation (draft; branch `feat/windows-oci-sync-foundation`).
- #399 — REA workflow and Colibri local inference (draft; branch `feat/rea-colibri-integration`).
- #395 — typed agent contracts, delegation registry, specialist profiles and OCI provider/router work (draft).
- #364 — unified first-run CLI, local-first provider defaults, shared guarded runtime and desktop packaging (open, not draft).
- #363 / #362 — desktop model selection and shared model catalog (draft, stacked).
- #359 — earlier model-selection/onboarding branch (draft and documented as diverged; do not merge wholesale).
- #358 / #366 — provider default/no-mock branches; #358 says its unique work should be consolidated into #364.
- #353 — Gemini and NVIDIA NIM providers (draft).
- #349 — guarded desktop chat (draft, based on an older onboarding branch).
- #340 — SQLite transactional state/audit migration (open; requires independent review and failure-mode acceptance).
- #395 and #400 are separate from main and must pass exact-head CI and integration review.

PR #364 documents an important architecture conflict: #363's separate desktop bridge constructs its own provider/agent, while #364's desktop shell delegates to the shared guarded runtime. Do not merge both paths blindly. Select one shared runtime boundary and port the useful model-selection UX into it.

PR #400's Windows sync UI and PR #364's desktop packaging also need consolidation. The sync PR's code is not automatically part of the desktop release.

PR #399's REA integration is a data-only workflow skill guide; native stdio MCP process launching is not implemented. Colibri is an optional local OpenAI-compatible text provider, not a bundled model runtime; Yuki does not install weights or automatically start Colibri.

## Open issues to turn into tracked acceptance work

The repository's open issue list includes:
- Expand Agent Skills compatibility with safe repository installs and managed updates.
- Complete Linux and Windows desktop packaging and release smoke tests.
- Product track: desktop app and guided first-run onboarding.
- Harden durable task idempotency and lease recovery.
- Serialize local persistence and define failure semantics.
- Rootless Podman acceptance and security sign-off for isolated upgrades.
- Approval security review and isolated upgrade workers.
- Local authentication database permissions.
- Upstream agent reuse audit: licenses, capability mapping and safe integration plan.
- Stabilize CI before merging agent organization work.

These issues should be deduplicated into a small number of milestone epics with explicit acceptance criteria.

## Recommended implementation order

### P0 — Release integrity and architecture consolidation
1. Freeze new feature PR creation temporarily; inventory and map every open draft to current main.
2. Choose one supported app composition root and one desktop runtime bridge.
3. Rebase/port PR #364's usable work onto current main; resolve provider/model-selection overlaps (#358/#362/#363/#366/#349) without parallel provider or execution paths.
4. Establish exact-head required CI and clean Windows/Linux install tests; fix failures before merging additional features.
5. Audit SQLite/JSON persistence concurrency, transactions, migrations, corrupt-store recovery and disk-full/locked-file behavior.

### P1 — First usable Yuki release
6. Complete real local Ollama inference on Windows and Linux; add optional Colibri after PR #399 review; keep OpenAI/Gemini/NVIDIA/OCI routes explicit and no-silent-fallback.
7. Finish conversational LLM loop: streamed response, bounded tool calls, fresh approvals, cancellation, context/memory, audit and independent verification.
8. Finish the desktop shell on the same runtime as CLI: first-run setup, provider/model settings, chat, tasks, skills, approvals, logs and diagnostics.
9. Finish Agent Skills repository import/update with provenance, safe resource handling, risk preservation and CLI/desktop shared state.
10. Publish a usable signed Windows installer and tested Linux installation/service instructions.

### P2 — Connected Windows + Oracle Cloud
11. Complete PR #400 sync protocol and merge it into the selected desktop architecture.
12. Implement server-side Oracle deployment: private HTTPS gateway, device authentication, revocation, least privilege, rate limits, encryption, secrets management, backups and monitoring.
13. Test two real devices through offline/online transitions, duplicate/replayed events, conflict resolution, revocation, restore and schema upgrades.
14. Add memory/conversation/skill sync only with explicit per-collection privacy and conflict policies.

### P3 — Advanced AI platform
15. Integrate PR #395's typed delegation and specialist runtime only after its resource, security and lifecycle review.
16. Build a curated 100-agent catalog incrementally; PR #395's 20 starter profiles are not a delivered 100-agent fleet.
17. Add REA native MCP stdio support only after server lifecycle isolation and tool approval; keep REA's workflow guide available separately.
18. Add agent evaluation, model routing/cost telemetry, retrieval evaluation and long-duration worker soak tests.
19. Complete optional voice, proactive scheduling, browser write actions and project builder behind separate permissions and release gates.

## Definition of usable and production-ready

### Personal-use beta
- Clean install on Windows and Oracle Linux.
- Real local model inference on each supported platform.
- Chat, provider selection, skills, tasks and local persistence work through the supported UI.
- No credentials in repository/config/logs.
- Backups can be restored.
- Exact-head CI passes and real-device smoke tests are recorded.

### Connected beta
- Pair Windows and Linux devices securely.
- Sync selected collections in both directions.
- Offline queue, retry, replay protection and conflict resolution pass tests.
- Device revocation and backup/restore are tested.
- OCI service has private network exposure, monitoring, resource limits and documented recovery.

### Production
- Independent security review, threat model and incident process.
- Signed/reproducible release artifacts, SBOM and dependency scanning.
- Supported upgrade/rollback paths.
- Load, soak, failure-injection and recovery tests.
- SLOs, alerts, audit retention and privacy controls.
- No capability advertised as supported without an acceptance test.

## Audit limitations

This audit used GitHub repository tree/file/PR/issue metadata and selected branch snapshots. GitHub code search returned no matches for common TODO/FIXME/NotImplemented markers, but that is not a definitive exhaustive source scan. No local clone or shell test execution was available in this audit. CI snapshots are time-dependent; verify exact-head Actions results before merging.
