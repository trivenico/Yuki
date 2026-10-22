# External agent capability audit — Hermes, OpenClaw, OpenJarvis and MYRAA

**Purpose:** learn from these projects and prioritize capabilities for Yuki without assuming that studying a repository means its code is integrated. This is an architecture comparison, not a claim of feature parity. External repositories change frequently; re-check the upstream docs and licenses before adopting code.

## Current evidence in Yuki

The model discovery sequence currently lives in open, unmerged PRs:
- Phase 62 — public Hugging Face metadata discovery: https://github.com/trivenico/Yuki/pull/208
- Phase 63 — model evaluation: https://github.com/trivenico/Yuki/pull/209
- Phase 64 — model registry/recommendations: https://github.com/trivenico/Yuki/pull/210
- Phase 65 — scheduled model scouting: https://github.com/trivenico/Yuki/pull/211
- Phase 66 — snapshot change detection: https://github.com/trivenico/Yuki/pull/212
- Phase 67 — persistent baseline and GitHub issue alerts: https://github.com/trivenico/Yuki/pull/213

These are proposed repository changes, not shipped default-branch features. Existing Yuki provider work and other capabilities require a separate source audit before asserting parity.

## Projects and useful capabilities

### Hermes Agent
Upstream: https://github.com/NousResearch/hermes-agent

Documented capabilities include persistent cross-session memory, skills created and improved from experience, multi-model/provider support, subagents, cron scheduling, many messaging channels, browser/web tools, MCP, voice and isolated terminal backends.

**Best ideas for Yuki:** versioned procedural memory/skills; explicit skill evaluation before promoting a learned skill; resumable scheduled tasks; subagents with isolated workspaces; a portable skills format; messaging adapters.

**Do not copy blindly:** self-modifying behavior needs review gates, skill provenance, tests, rollback and strict tool permissions. Persistent memory must be scoped per project/user and deletable.

### OpenClaw
Upstream: https://github.com/openclaw/openclaw

Documented capabilities include a self-hosted Gateway, many chat channels, provider/model plugins and failover, per-agent sessions/workspaces, skills, plugins, cron/hooks/webhooks, companion device nodes, voice/media, a control UI and local state.

**Best ideas for Yuki:** a provider/channel adapter contract; durable session and agent isolation; event-driven integrations; permissioned plugins; operator control UI; channel pairing and authorization.

**Security caveat:** upstream documentation warns that host tools can run on the host unless sandboxing is configured. Yuki must default to least privilege, isolate untrusted jobs and avoid exposing a shared gateway across mutually untrusted users.

### OpenJarvis
Upstream: https://github.com/open-jarvis/OpenJarvis

Documented capabilities include a local-first stack with independently configurable model intelligence, inference engines, agents, tools/memory and learning; multiple local runtimes; hardware-aware model selection; OpenAI-compatible serving; offline-first memory and telemetry; scheduled agents; and trace-driven evaluation/optimization.

**Best ideas for Yuki:** explicit model/engine/agent/tool/memory contracts; local-first mode; hardware and VRAM-aware model recommendations; per-task accuracy/latency/cost/energy measurements; evaluation-gated prompt/skill optimization.

**Important principle:** optimize local/cloud choices against measured quality and privacy needs, not a blanket claim that a small local model can replace every frontier model.

### MYRAA / MYRA — name is ambiguous
One matching public repository is https://github.com/Vishwajeetsrk/Myraa. Other repositories use similar names, so confirm the exact upstream project before adopting any code.

That repository describes a desktop assistant with intent classification, model cascading, local retrieval/memory, a dynamic skill catalog, hot-loaded plugins, public API connectors, a 3D avatar and mobile remote access. Other similarly named projects focus on real-time voice, screen vision and desktop automation.

**Best ideas for Yuki:** intent-based task routing; bounded provider fallbacks; a plugin manifest with declared permissions; source-cited retrieval; optional voice/desktop UI as separate clients; authenticated mobile pairing.

**Security caveat:** desktop control, clipboard, screen capture and remote control are high-impact permissions. Keep them opt-in, scoped, visible to the user and auditable. Do not grant skills unrestricted desktop access by default.

## Capability adoption matrix

| Capability | Current Yuki status | Recommended implementation |
|---|---|---|
| Multi-provider model routing | Partial / not fully audited | One provider contract, model catalog, explicit fallback policy and deadlines |
| Local-first inference | Not verified as end-to-end | Ollama/local runtime adapter, health checks, hardware-aware selection |
| Persistent project memory | Not verified as parity | Scoped SQLite first, provenance, retrieval tests and retention controls |
| Reusable skills | Not verified as parity | Portable skill manifests, source/license tracking, permission declarations, offline tests |
| Agent delegation | Not verified as production-grade | Task graph, budgets, cancellation, isolated worktrees and human approval |
| Scheduled automation | Phase 65/67 proposed for model scouting only | Durable scheduler with idempotency, execution ledger and alert delivery |
| Browser and messaging channels | Not verified as integrated | Pluggable adapters with allow-lists, pairing, rate limits and secret isolation |
| Voice, screen and desktop actions | Not verified as integrated | Optional client modules, explicit consent and per-action permissions |
| Learning / self-improvement | Model evaluation/registry proposed in PRs #209–#210 | Trace-based evaluations; accept changes only when tests show no regression |
| Model discovery | PRs #208 and #211 | Scheduled public metadata scout |
| Change alerts | PRs #212 and #213 | Snapshot diff and GitHub issue alerts after merge and permissions are enabled |

## Recommended implementation order

1. Stabilize and merge the existing model-discovery dependency chain after all checks pass.
2. Implement a provider/model capability registry and tested fallback rules.
3. Implement the durable task runtime: job state, budgets, timeouts, cancellation, approvals and isolated workspaces.
4. Implement scoped memory and portable, reviewable skills.
5. Add local-first engine selection and model/skill evaluation telemetry.
6. Add browser, messaging and GitHub adapters with explicit permission boundaries.
7. Add voice and desktop automation only as optional, separately permissioned clients.

## Adoption policy

Prefer learning from interfaces and architecture over importing whole applications. Before adopting any dependency or skill, record its upstream URL, pinned revision, license, maintainer activity, dependencies, security posture, permissions, tests and rollback plan. Never auto-install a community skill or execute instructions found in model cards, crawled pages or repository content without review.
