# Yuki external-reference audit (living document)

This register keeps the external projects used as product/engineering references visible during Yuki's production work. The current Project memory identifies these names from prior Yuki discussions; the exact original user-pasted URLs were not recoverable from the indexed conversation/library search in this pass. Links below are canonical public references verified during this audit, not a claim that these are byte-for-byte the original URLs.

## Reference register

| Reference | Canonical source | Useful patterns to evaluate for Yuki | Adoption guardrails |
|---|---|---|---|
| Hermes Agent (possibly the earlier “Hermer” reference) | https://github.com/NousResearch/hermes-agent | Persistent memory, session recall, skill creation/improvement, terminal UX, scheduled jobs, delegated subagents, provider choice, explicit tool approval and container isolation | Do not copy a large tool surface without threat-modeling permissions, secrets, terminal isolation, and cross-session identity. |
| OpenClaw | https://github.com/openclaw/openclaw | Personal assistant across channels/devices, local ownership of state, pluggable models and agent harnesses | Channel integrations require per-channel authentication, pairing/allowlists, secret handling, and abuse limits. Yuki remains single-owner and local-first. |
| OpenJarvis | https://github.com/open-jarvis/OpenJarvis | Local-first model engine, hardware-aware model selection, agent modes, tools and memory, evaluation of latency/energy/cost, local API/UI | Treat as an architecture and evaluation reference, not as evidence that Yuki already supports those integrations. Preserve cloud opt-in and transparent provider status. |
| Stonic AI | https://stonicai.com/ | JARVIS-like desktop experience, character/voice, persistent memory, visible agent roles/status, computer agent with visible steps and stop control | Product/UX quality reference only. Do not claim parity. Computer-use actions need visible execution, interruption, least privilege, and explicit approval for consequential actions. |
| Google Jules | https://jules.google/docs/ and https://blog.google/innovation-and-ai/models-and-research/google-labs/jules-now-available/ | Asynchronous repository tasks, planning before execution, isolated VM, parallel tasks, visible diffs, tests and PR-based delivery | Yuki needs durable job state, bounded execution, workspace isolation, cancellation, audit events, and test evidence before claiming equivalent autonomous engineering. |
| Myrra (user previously referred to as “Myraa”) | https://www.myrra.ai/ | Voice-first live meeting participation, retrieval of relevant context, real-time action items | This is a meeting/enterprise voice-agent product, not necessarily the exact historical reference. Treat as optional inspiration; do not add meeting recording or external communications without explicit owner setup and consent. |

## Feature-to-Yuki audit

Status reflects the repository audit context available for this document, not a fresh end-to-end test run.

- **Local-first personal assistant:** partial. Yuki has local CLI and memory foundations, but provider defaults, integrations, and deployment boundaries must be configured and validated.
- **Persistent memory and recall:** partial/scaffolded. Local notes and learning records exist; robust semantic retrieval, retention policy, backup/restore, and sensitive-data controls need separate qualification.
- **Skill/tool ecosystem:** partial. An allow-listed tool set exists. The skill/plugin runtime is not a security sandbox; Python imports can execute with Yuki's process privileges.
- **Multi-agent delegation:** partial/scaffolded. Some coordinator/worker workflow modules and PRs exist; end-to-end orchestration, failure recovery, cancellation, and permissions need verification.
- **Computer/OS control:** not qualified for broad autonomous use. Keep destructive and external side effects behind explicit approval and constrained execution.
- **Voice/proactive presence:** partial/optional. Do not claim a reliable always-on wake-word experience without measured tests on target hardware.
- **MCP/external integrations:** partial. MCP prototypes are not proof of a fully connected runtime. Current experimental ChatGPT bridge is intentionally loopback-only.
- **Autonomous coding:** partial. Repository workflows/PRs exist, but safe workspace isolation, deterministic tests, durable jobs, and verified results are release gates.
- **Provider flexibility:** partial. Mock is the safe default; local Ollama and configured compatible adapters need runtime-level validation. Do not describe unsupported browser subscription login as integrated.
- **Production security/reliability:** incomplete. Authentication entry-point integration, audit durability, isolation, backups/recovery, and independent security review remain gates.

## Rules for future reference-driven implementation

1. Keep the user's intended product boundary: private, single-owner, JARVIS-like personal assistant; not a public SaaS.
2. For each reference feature, record: source URL, observed capability, target Yuki module, implementation status, test evidence, security impact, and explicit non-goals.
3. Do not mark a feature implemented merely because a module, command, or UI entry exists. Require an end-to-end test and honest failure state.
4. Do not bulk-import community skills or execute remote plugins without source review, permission declarations, integrity checks, and an isolated execution boundary.
5. Prefer small, reviewable PRs with exact-head CI evidence. Do not merge a security-sensitive PR on the basis of a PR description or stale workflow results.
6. Reconcile this register against the original URLs in earlier project conversations when they become available; where names are ambiguous (for example “Hermer” or “Myraa”), verify identity before making feature decisions.
