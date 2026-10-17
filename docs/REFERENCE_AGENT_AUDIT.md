# Reference repository audit for Yuki

Audit date: 2026-10-17. This is a feature/architecture review of public repository documentation and file trees, not a security audit of every source file. Yuki must re-check upstream changes before adopting any specific implementation.

## Repositories reviewed

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — MIT, Python. Its README highlights agent-curated memory, skill creation/improvement, cross-session search, provider switching, scheduled automations, terminal backends, and chat integrations.
- [AutoClaw](https://github.com/tsingliuwin/autoclaw) — MIT, TypeScript. Its README highlights headless deterministic tool execution, step/time caps, destructive-command and credential-file guards, retries, JSON event output, web reading/screenshots, and deterministic offline image/PDF rendering.
- [OpenClaw](https://github.com/openclaw/openclaw) — MIT, TypeScript. Its README highlights a gateway/control-plane architecture, swappable model providers, multiple channels, plugin/skill extension, local state and credentials, companion-device capabilities, pairing controls, and explicit sandbox/security guidance.
- [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) — Apache-2.0, Python. Its README highlights local-first inference, resource-aware evaluation, presets, scheduled operations, persistent memory, skill catalogs/import, and learning/trace optimization.

## What Yuki should adopt (in priority order)

### P0 — reliability and portability
1. Portable health check/doctor that distinguishes core readiness from optional integrations.
2. Portable data root, OS-specific launchers, encrypted-drive guidance, and explicit backup/restore boundaries.
3. Reproducible core dependency installation and CI tests on Windows, Linux, and macOS.
4. Provider interface with safe configuration validation and a live connection test only when explicitly requested.
5. Structured JSONL run events and correlation IDs without recording secrets or full sensitive prompts by default.

### P1 — safe agent runtime
1. Keep the registered-skill model and default-deny permissions. Add bounded execution budgets, deadlines, cancellation, and explicit confirmation for destructive actions.
2. Add a provider-backed tool-calling loop only after validating tool schemas, result limits, turn limits, and prompt-injection boundaries.
3. Add a channel adapter interface; start with one channel (e.g. Telegram) and secure sender pairing rather than implementing many untested stubs.
4. Add a durable scheduler with restart recovery, duplicate prevention, opt-in jobs, and a visible job history.
5. Improve memory with source/provenance, user review, export/delete controls, and explicit memory-write policy. Keep self-modification separate from memory.

### P2 — capability breadth
1. Standards-based skills with metadata, version/source/license records, dependency declaration, and a review gate before enabling third-party skills.
2. Browser automation in a separate restricted context; allow-listed hosts, downloads disabled by default, no shared personal browser profile, and explicit approval for side effects.
3. Local-first model routing with measured latency/RAM and graceful cloud fallback only when configured.
4. Research workflows with citations, source capture, bounded fetches, and untrusted-content labeling.
5. Voice interruption and multi-language support only for installed and tested STT/TTS models.

## Do not copy blindly

- Do not copy an entire agent runtime or large third-party skill bundle into Yuki. Prefer small, isolated implementations and test them against Yuki's permission model.
- Upstream license metadata is useful but does not automatically license every dependency, asset, or skill that a project imports. Preserve attribution/license notices and audit each file/dependency before copying source.
- Treat third-party skills as executable code and prompt content as untrusted. Review source, pin versions/commits, record provenance, scan secrets, and require approval before enabling.
- Avoid declaring features complete merely because a module or README claims them. Require tests, a working runtime path, integration tests, and documented limitations.

## Current Yuki gaps confirmed from its README/runtime layout

- No live LLM-driven tool-calling loop; mock provider remains a foundation rather than a full assistant.
- MCP client is not connected to runtime commands and has no stdio/SSE-only transport.
- Workflow engine is a bounded step runner, not a durable scheduler.
- Browser integration is restricted page reading, not full interaction automation.
- Knowledge graph CRUD/export does not implement automatic graph “dreaming.”
- Voice interruption is not connected to microphone VAD; female/natural voice depends on a compatible installed voice model.
- Portability is not yet turnkey: local virtual environments and optional binaries/models are OS-specific, and the existing install scripts target the checkout rather than a separately managed encrypted data volume.

## Sources

- https://github.com/NousResearch/hermes-agent
- https://github.com/tsingliuwin/autoclaw
- https://github.com/openclaw/openclaw
- https://github.com/open-jarvis/OpenJarvis
