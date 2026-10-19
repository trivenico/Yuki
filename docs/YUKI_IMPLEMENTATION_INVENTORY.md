# Yuki implementation inventory and integration plan

Updated: 2026-10-19. Scope: current GitHub `main`, open Yuki implementation PRs, and Yuki architecture/feature descriptions available in the project conversation and Library. This is an evidence-based backlog, not a claim that every past chat message or local working-tree change is available in GitHub.

## 1. Source-of-truth and not-yet-pushed work

| Item | Location/status | Consequence |
|---|---|---|
| Phase 1 resilience and self-healing files (`core/resilience.py`, `core/self_healing.py`) | Discussed in earlier project conversation; downloadable package generation failed; these files were not verified in GitHub tree | Re-implement/reconcile as a dedicated change before calling the runtime complete |
| Phase 12 role/council catalog + feature audit | Draft PR #12: https://github.com/trivenico/Yuki/pull/12 | Not on `main`; depends on review and CI |
| Phase 13 SQLite job queue | Draft PR #13: https://github.com/trivenico/Yuki/pull/13 | Not on `main`; Phase 14 branch builds on its head so the queue integration can be reviewed together |
| Phase 14 guarded queue bridge | Current branch | Not on `main`; separate draft PR will document its dependencies |
| Earlier local-only edits or chat-generated snippets | Cannot be proven pushed without an actual local workspace or explicit commit/ref | Compare local git status/log when the working copy is available |

Do not merge a child PR until its parent implementation is merged or the PR base is deliberately updated. Never report a draft PR as released.

## 2. Existing repository architecture found on `main`

Observed modules include:
- Runtime and task handling: `core/agent.py`, `core/task_agent.py`, `core/planner.py`, `core/role_engine.py`, `core/capabilities.py`, `core/config.py`.
- Permissions and approvals: `core/permissions.py`, `core/pending_approvals.py`, `core/tiered_autonomy.py`, `core/prompt_guard.py`.
- Skills and integrations: `skills/`, `core/plugin_loader.py`, `core/mcp_client.py`, `automation/workflow_engine.py`, `automation/browser.py`, `automation/git_manager.py`.
- Memory and companion: `core/vector_memory.py`, `companion/memory.py`, `companion/persona.py`, `companion/mood_engine.py`, `companion/proactive.py`, `companion/evolution.py`.
- Defensive/security components: `defense/firewall.py`, `defense/ids.py`.
- Packaging/docs: `config.yaml`, `.env.example`, `requirements.txt`, Docker files, installation and security documentation.

Presence of these files does not prove production integration. The README and capability-status documents should remain the source of feature truth after each audit is updated.

## 3. Feature status ledger

Status labels: **present** = source file exists; **partial/unverified** = integration, runtime behavior or dependencies not established; **planned/not evidenced** = not found in the reviewed main tree or not proven end-to-end.

| Feature from project conversations and requested specification | Evidence/status | Integration work remaining |
|---|---|---|
| Terminal chat and main runtime | Present; end-to-end launch unverified | Define one runtime composition root and deterministic shutdown |
| ReAct/tool loop | Agent/task-agent modules present | Bind planner, provider, registered skill, approval, audit, result verification |
| Local Ollama model | Provider code referenced in repository docs; current runtime availability unverified | Provider contract, timeouts, model readiness, resource limits |
| Hybrid/local+cloud model routing | Prior audit says local-first provider exists | Re-check allow-listed task and user approval at every cloud-send call site; prevent silent fallback |
| Human approval | Permission gate and persistent approval store present | Wire identity/session binding to actual CLI/runtime and consequential skills |
| Retry/circuit breaker/resilience | Prior Phase 1 request; files not verified on main | Implement and test bounded retries, deadlines, circuit breaker and graceful degradation |
| Auto-heal Ollama/Chroma/voice | Prior Phase 1 architecture request; end-to-end behavior unverified | Health probes and allow-listed recovery; no arbitrary shell or destructive repair |
| Prompt injection defenses | Heuristic prompt guard present | Treat as signal, isolate tools and data; add adversarial tests and output validation |
| Audit log and output redaction | End-to-end coverage unverified | Structured redacted events, correlation IDs, retention, tamper considerations |
| Skill auto-discovery | Plugin loader/registry present | Keep untrusted plugin loading disabled by default; verify interfaces, origin and isolation |
| Hot reload within 10 seconds | Not established end-to-end | Trusted signed/approved skills, isolated process and safe reload/rollback |
| Persistent job queue | Added in PR #13 | Integrate dispatcher, lease fencing, shutdown, concurrency and recovery |
| Multi-agent/32 councils | Role metadata in PR #12, real workers not evidenced | Queue-driven bounded worker pool, model budgets, task state, reviewer and provenance |
| 25+ enterprise roles | Role metadata draft in PR #12 | Role instructions + permitted skill IDs, no role-based permission escalation |
| 80+ skills | Broad catalog is a requested target; registry presence does not establish each skill | Generate per-skill inventory from actual files, mark implemented/stub/planned, contract tests |
| Earning research and companion | User priority; some skills/catalog references exist | Cited research, opportunity scoring, cost/time/risk, experiment log and measured outcomes |
| Personal memory and knowledge graph | Memory/vector modules present; full retention lifecycle unverified | Export, correction, deletion, backup/restore, encryption and tenant/user boundaries |
| Voice STT/TTS/wake word/speaker identity | Optional components referenced in project blueprint | Hardware smoke tests, consent, visible mic state, voiceprint retention/deletion |
| Camera/screen observation | Requested but not evidenced as safe always-on implementation | Off by default, explicit session opt-in, capture indicator, minimal retention |
| Vision/OCR/multilingual | Requested; performance claims not independently verified | Model selection, offline tests, benchmark dataset and measured accuracy by language |
| Browser/desktop/cross-app | Automation components exist; safe full workflow unverified | Domain/action allowlists, isolated browser, confirmation before submission/purchase/delete |
| Content/image/video generation | Requested skill families; end-to-end availability unverified | Provider adapters, licensing checks, costs, artifact storage and failure handling |
| Financial, crypto, ecommerce and payments | Requested skill families; capabilities vary by integrations | Read-only first, explicit trade/purchase approval, credentials and compliance boundaries |
| Cybersecurity | Defensive modules and a `hackback/` folder exist | Keep offensive/destructive actions disabled; authorization scope, lab isolation, review logs |
| Telegram/Discord/Slack/WhatsApp | Requested integrations; credentials and live delivery unverified | Per-channel opt-in, scopes, message preview, secret storage and delivery status |
| Self-evolution/Darwin improver | `companion/evolution.py` present; safe workflow unverified | Proposal-only patch, separate branch, sandbox tests, diff review, rollback; no self-merge |
| Workflow engine/scheduler/daily briefings | Declarative workflow engine present; scheduler integration unverified | Persistent scheduler, timezone rules, idempotency, retries and opt-out |
| Web API/dashboard/3D office | Not proven integrated in the reviewed Python runtime | Authenticated API, rate limits and event model before visualization |
| Portable USB and cloud deployment | Docker/install/docs present | Clean install on target OS; persistent volumes, least privilege, backup, monitoring and upgrade path |
| Secrets/dependencies/CI | `.env.example`, `.gitignore`, workflow present | Secret scanning, dependency pin/review, lockfile strategy, CI full-suite and release artifacts |

## 4. Next integration contract

```text
CLI / future API
  -> Runtime composition root
  -> Planner (proposes bounded plan)
  -> Policy + trusted identity/session
  -> Approval coordinator (when needed)
  -> Persistent job queue
  -> Bounded worker pool
  -> Registered task handler / skill
  -> Permission re-check immediately before external effect
  -> Output guard + concise verification
  -> Redacted audit event + durable job result
  -> CLI status/report
```

Provider routing and memory are injected interfaces. Skills do not get raw environment secrets or unrestricted shell access. Every external side effect must be scoped, approved as needed, and auditable.

## 5. Coding-first backlog, then dedicated acceptance test phase

1. Reconcile resilience/self-healing with the actual runtime interfaces.
2. Connect job queue to a registered-handler dispatcher (this phase adds the guarded bridge).
3. Add a single composition root that constructs provider, registry, approvals, memory, queue and companion once.
4. Add a worker lifecycle with bounded concurrency, lease fencing, cancellation, shutdown and resource budgets.
5. Produce an auto-generated skills manifest from actual files and standardize every skill contract.
6. Connect role profiles to allowed skill IDs and task envelopes; role profiles never grant authority.
7. Complete provider routing, memory lifecycle, audit/redaction and proactive scheduling interfaces.
8. Complete the terminal MVP, then optional voice/browser/API integrations.
9. Only after the implementation backlog is connected: run fresh-install, unit, integration, security, performance, privacy, recovery and deployment acceptance suites; fix and repeat until required checks pass.

## 6. Acceptance criteria

- No unregistered task handler is executable.
- Authorization is checked at submit and immediately before handler execution.
- Workers are bounded and stale leases cannot produce accepted results.
- No secret or unredacted sensitive payload appears in logs.
- Plugins and self-modification are opt-in and isolated.
- Feature inventory distinguishes source present, integrated, tested and production verified.
- CI passes on the exact PR head, review is complete, and deployment smoke tests pass before merge/release.
