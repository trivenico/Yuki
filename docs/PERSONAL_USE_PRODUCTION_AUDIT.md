# Yuki personal-use production readiness audit

Audit date: 2026-10-17. Scope: current `main` repository files, configuration, runtime wiring, voice adapters, skills, backup scripts and GitHub Actions. This is a source review, not a local execution, penetration test, or independent security certification.

## Executive assessment

Yuki has a useful guarded Python foundation: explicit approvals, a skill registry, local memory, a review-only evolution cycle, opt-in voice, limited defensive tools, portable path configuration and backup scripts. It is **not yet a complete conversational personal AI agent**. The current runtime is still command-driven; provider metadata lists planned providers, but only a mock provider adapter is implemented, and no LLM-driven tool loop is connected.

For personal use, prioritize privacy, reliable installation, safe tool boundaries, data recovery and honest capability reporting before adding more features.

## Findings by severity

### P0 — Resolve before storing private personal data

1. **GitHub repository visibility**: the repository API reports `private: false`. This is a public source repository, not a private personal workspace. Change visibility to private in GitHub settings; inspect history and revoke any credential that was ever committed. This audit does not change repository visibility.
2. **Sensitive data protection**: `.gitignore` reduces accidental future commits but does not protect data in the repository history, issue text, Actions logs, or files already pushed. Never commit real health data, memory, recordings, OAuth tokens, provider keys or browser profiles.
3. **Backup encryption and restore proof**: backup scripts create plain `.tar.gz`/`.zip` archives. Use encrypted storage, a separate physical backup, and test restoration. Stop Yuki before backup because JSON data is not transactionally snapshotted.

### P1 — Important engineering gaps

4. **No real LLM conversation/tool-calling loop**: `providers/mock.py` is a deterministic mock; `yuki_providers.py` labels OpenAI, Anthropic, Gemini and local as not implemented. Provider configuration alone does not connect these providers. Do not call Yuki a complete AI assistant until a real provider adapter and bounded tool loop exist.
5. **Voice preferences are configuration, not end-to-end behavior**: STT uses faster-whisper when the optional dependency/model is available and returns its detected language. The natural-female voice label is a preference only; actual TTS voice depends on the installed Piper model or OS voices. Spoken-reply-every-time cannot occur until the conversation response loop is connected. Wake word is not continuous listening.
6. **External SSD portability is partial**: launchers use host Python or a platform-specific `.venv`; optional models, audio, Playwright/browser, Nmap and Docker have separate requirements. Cross-platform packaging and absent/read-only-drive handling are not yet verified.
7. **Health and memory are sensitive local JSON files**: add explicit export/delete/retention controls and ensure permissions are restrictive where supported. Avoid automatically importing external content into memory.
8. **Reminder/evolution scheduling is process-bound**: the CLI's reminders are checked when commands run, not a durable notification service. The evolution scheduler runs only when manually launched and the computer/process remain active.
9. **Network and browser skill boundaries need defense-in-depth tests**: review DNS resolution, redirect chains, private/link-local addresses, file URLs, browser downloads and pages that attempt prompt injection. Treat web content as untrusted data; never let a page grant itself permissions.
10. **Backup scripts need platform execution testing**: GitHub Actions currently runs unit tests on Ubuntu/Python 3.11. Add Windows and macOS jobs and smoke tests for backup creation, empty folders, paths with spaces, retention and restore. Never claim those platforms are tested just because scripts exist.
11. **Dependency/supply-chain coverage is limited**: core requirements are minimal, while optional features use separate requirements files. Add dependency vulnerability scanning, pinned/controlled optional dependency updates, secret scanning and license review before using optional integrations.

### P2 — Quality and maintainability

12. Add structured, redacted audit events for approvals, tool results and provider failures; never log prompts or personal payloads by default.
13. Add schema versions and migration tests for persistent JSON stores; fail closed on corrupt files and preserve recovery copies.
14. Add capability/readiness checks that distinguish implemented, configured, available, tested and unsupported states.
15. Add cancellation/timeouts and resource limits consistently to every optional integration.
16. Add tests for path traversal, symlink escapes, malformed state, concurrency and interrupted writes across memory, health, knowledge and evolution stores.
17. Personal-use mode should default to local-only, no background microphone, no external accounts, no automatic shell execution, and no outbound network unless explicitly configured.

## Implemented during this audit

- Tightened `ollama_base_url` validation. It now parses the URL instead of using vulnerable hostname-prefix checks, allows plain HTTP only for loopback, permits HTTPS endpoints, rejects embedded credentials, query/fragment components, malformed ports, and rejects hostname-prefix spoofing such as `127.0.0.1.attacker.example`.
- Added regression tests for spoofed hostnames, remote HTTP, credentials, malformed ports and valid loopback/HTTPS URLs.

These changes were committed separately. CI results must be checked for the latest commit before considering them verified.

## Personal-only completion roadmap

### Phase A — Private and recoverable
- Make GitHub repository private and review commit history/secrets.
- Encrypt data volumes and backup destinations.
- Test backup and restore on real Windows/Linux/macOS targets.
- Document how to delete memory, recordings, OAuth tokens, browser profiles and backups.

### Phase B — Core reliability
- Add CI on Windows, Linux and macOS.
- Add static checks, dependency/secret scanning, coverage and filesystem security tests.
- Add state schema versions, atomic writes and restore validation to all stores.
- Add `/doctor` to explain missing optional dependencies/models without attempting installation.

### Phase C — Actual assistant
- Implement one real provider adapter first, with explicit environment-based credentials and redacted errors.
- Add bounded conversation context and a tool-call protocol that can invoke only registered skills.
- Validate every tool call against schema, permissions, risk, timeouts and user approval.
- Keep mock provider for deterministic tests; add fake-server tests and explicit live-provider smoke tests.

### Phase D — Voice
- Wire user speech -> STT -> response -> optional TTS only after provider loop exists.
- Keep microphone and voice mode off by default; show recording status and require explicit activation.
- Select and test a real local voice model by language, license and platform; never claim a model or language is supported until tested.
- Implement barge-in with cancellable playback and tested audio-device behavior.

### Phase E — Personal automation
- Add durable, opt-in scheduler with idempotency, restart recovery, and a visible activity log.
- Require per-action approval for file writes, browser changes, messages, security scans and destructive operations.
- Run browser and MCP integrations in least-privilege isolated contexts; external content cannot override system permissions.

## Acceptance criteria for personal use

- Repository private and no live secrets in Git history.
- Fresh install works from documented steps on target operating systems.
- All CI jobs pass; optional features report unavailable rather than crash when dependencies/models are missing.
- No unapproved filesystem, shell, network, messaging or security action.
- Personal data can be exported, deleted and restored from encrypted backups.
- Provider, language, voice, and integrations show accurate readiness status.
- At least one real provider path, one tested conversation loop and one tested end-to-end voice path work on the user's target machine before declaring those capabilities ready.
