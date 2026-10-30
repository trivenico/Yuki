# Upstream capability audit for Yuki

Audit date: 2026-10-30. This is an architecture and feature review of the public repositories below, not a claim that their entire codebases were imported or independently security-audited.

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

## Implemented upstream reuse — 2026-10-30

### OpenJarvis benchmark statistics

- Source: [`src/openjarvis/bench/_stats.py`](https://github.com/open-jarvis/OpenJarvis/blob/336a68ac3b6206e3f3037c5c6d9d40722c4ab4cb/src/openjarvis/bench/_stats.py)
- Reused file: `providers/evaluation_stats.py`
- Integration: `providers/model_evaluation.py` now reports mean, p50, p95, min, max and standard deviation for successful inference latency samples.
- License: Apache-2.0. The complete upstream license is included at `licenses/Apache-2.0-OpenJarvis.txt`; see `THIRD_PARTY_NOTICES.md`.
- The statistics helper is copied from upstream with an attribution header. Yuki's integration and surrounding evaluation code remain Yuki-owned code.

### OpenClaw / Hermes portable skill format

- OpenClaw's [SKILL.md format documentation](https://github.com/openclaw/openclaw/blob/80b9e7b71fb262a53a37bda6d29c32e4e1ef0f8d/docs/tools/skills.md) and Hermes Agent's [example skill](https://github.com/NousResearch/hermes-agent/blob/088f657ecf77674c7b8e2ededf4e3576d82e042c/optional-skills/software-development/subagent-driven-development/SKILL.md) informed Yuki's Markdown importer in `core/skill_packs.py`.
- This is a compatibility implementation of the documented frontmatter format, not copied upstream parser code.
- Imported skills are converted to Yuki's existing data-only JSON pack format. Unknown frontmatter fields, duplicate keys, YAML aliases, oversized content and unsafe names are rejected.
- Imported risk defaults to high; metadata never grants tool, shell, network or account permissions. Existing approval and audit gates remain authoritative.
- Upstream skill content is not automatically downloaded or executed.

### Reuse boundaries

- Hermes' async delegation implementation is tightly coupled to its session gateway, worker ledger, context propagation, completion queue and state database. We are reusing its bounded delegation and recovery design patterns, not copying that implementation into Yuki.
- OpenClaw's gateway and plugin host are TypeScript services with their own transport and authority model. They are not safe drop-in Python modules; Yuki will adopt isolated contracts and adapters rather than importing the whole gateway.
- OpenJarvis' complete inference and benchmark stack has substantial optional dependencies and platform constraints. Only the small, directly useful statistics helper is imported in this milestone.
