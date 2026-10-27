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


## Yuki product architecture: desktop, CLI, and learning loop

### Product direction

Yuki should have one shared runtime with multiple thin interfaces, not separate assistants with separate memories or permissions:

- **Core runtime**: provider contract, conversation/session service, bounded task planner, skill registry, policy/approval engine, durable task queue, audit log, memory and evaluation.
- **CLI**: interactive chat, one-shot prompts, task planning/execution, provider status, skill discovery, memory search, approvals, diagnostics, logs, and scripted/headless operation.
- **Desktop**: chat, task inbox and approvals, activity/audit timeline, provider and model selection, memory/learning review, settings, voice controls, and system readiness. The UI must call the same core services as the CLI.
- **Learning loop**: capture explicit feedback and verified task outcomes; redact secrets and minimize data; retrieve relevant lessons; evaluate proposed changes on a fixed regression set; create a reviewable proposal. It must not silently rewrite its own source, grant itself permissions, or promote an update without tests and approval.

### Main files and ownership boundaries

| Area | Existing main files | Required next step |
|---|---|---|
| Provider entry | `providers/base.py`, `providers/factory.py`, `yuki_agent.py` | Make real configured provider the runtime default; inject mocks only in tests |
| Tool-using task loop | `core/task_agent.py`, `core/agent.py`, `core/guarded_skill_runtime.py` | One shared task service, strict schemas, budgets, approvals, audit and cancellation |
| Persistent memory | `companion/memory.py`, `core/vector_memory.py` | Schema/versioning, source attribution, retention, export/import, backup/restore tests |
| Learning and upgrades | `companion/evolution.py`, `core/self_healing.py` | Feedback → lesson proposal → offline evaluation → human-approved merge; no self-modifying runtime |
| Runtime health | `core/readiness.py`, `core/resilience.py`, `core/recovery_audit.py` | Machine-readable readiness and real smoke tests for every claimed integration |
| CLI | `main.py`, `yuki.py` | Consolidate duplicate entrypoints behind one CLI command surface and shared application services |
| Desktop | PR #32 and stacked PR #349 | Merge/rebase only after checks; keep desktop as a client of shared services, not a parallel agent |
| Storage / queue | durable task engine and SQLite state work in open PRs | Resolve audit/idempotency reviews and cross-platform concurrency tests before autonomous scheduling |

### Incremental delivery gates

1. **Runtime truthfulness**: no implicit mock provider in a user-facing default; no feature marked available just because its module imports.
2. **Unified CLI**: one entrypoint, provider status, chat, plan, approve/deny, tasks, memory, doctor and test commands; consistent exit codes and structured output.
3. **Desktop MVP**: onboarding, chat, model/provider selection, tasks/approvals, logs and settings; local loopback bridge only if needed, authenticated local IPC, strict origin/CSRF boundaries, no exposed arbitrary shell endpoint.
4. **Learning MVP**: opt-in feedback/outcome capture, redaction, bounded local lesson store, retrieval, explainable proposals and offline regression evaluation.
5. **Supervised engineering agent**: repository allow-list, isolated worktree, scoped file diffs, tests/lint/type/security checks, patch review, branch/PR creation, human merge approval, rollback path.
6. **Release gate**: Windows CI plus Linux CI, backup/restore and migration tests, provider smoke tests, prompt-injection tests, dependency/license review, security review and release artifacts.

### Inspiration and non-goals

Use Hermes/OpenJarvis patterns for CLI ergonomics, persistent procedural skills, evaluation and local-first operation; use OpenClaw-style gateway/device architecture only when a real channel is implemented; use a polished desktop shell with a clear task/activity model rather than duplicating the agent inside the UI. Do not wholesale-copy upstream code without checking the exact file license and dependencies. Avoid unattended shell execution, hidden persistence, unreviewed self-upgrades and permission escalation.
