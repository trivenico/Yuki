# Upstream capability audit for Yuki

Audit date: 2026-10-17. This is an architecture and feature review of the public repositories below, not a claim that their entire codebases were imported or independently security-audited.

## Upstream projects reviewed

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — README, project root and LICENSE. The repository describes persistent memory/procedural skills, model/provider configuration, messaging gateways, MCP, cron, context files, toolsets and interruptible CLI work. Its LICENSE is MIT.
- [AutoClaw](https://github.com/tsingliuwin/autoclaw) — README, safety notice, root and LICENSE. It emphasizes headless/container automation, portable skill packages, recipes/batch execution, OpenAI-compatible endpoints and browser tooling. Its LICENSE is MIT. Its own safety notice explicitly warns that unattended model-generated shell execution can damage systems or disclose credentials; Yuki should not copy its unattended-execution defaults.
- [OpenClaw](https://github.com/openclaw/openclaw) — README, root and LICENSE. It emphasizes cross-device assistant operation, messaging channels, provider configuration, extensions/skills, gateway and device nodes. Its LICENSE is MIT.
- [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) — README, root and LICENSE. It emphasizes personal-device/local AI, selectable agents, skill packages, deep research, scheduled monitoring, model/runtime evaluation and deployment presets. Its LICENSE is Apache-2.0.

- [Stonic AI](https://stonic.ai/) — product-pattern reference supplied for desktop-first companion interaction and visible activity. This is a product UX reference only, not a source-code or license audit.

## Feature decisions for Yuki

| Pattern | Existing Yuki baseline | Recommended next action |
|---|---|---|
| Guided first-run onboarding | PR #32 provides a terminal wizard and private user config; provider endpoint syntax is checked but live inference is not verified during setup | Add a real, bounded provider connectivity/model check, resumable desktop steps, clear privacy disclosure, and safe retry/recovery |
| Desktop companion experience | Optional PyWebView shell provides runtime/skill status and plain-text provider chat; cloud prompts require explicit per-message confirmation and a provider-side approval callback | Add visible task activity, reliable stop/cancel semantics, session history controls, opt-in voice, accessibility, and signed installers; keep skill execution outside desktop chat until an authenticated approval API exists |
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
