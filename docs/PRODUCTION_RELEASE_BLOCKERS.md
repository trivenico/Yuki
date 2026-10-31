# Phase 137 — Production release blockers and acceptance gate

Audit baseline: `main` at `dd9c88b524bf6e891ce0e92f2324ca4184ab3ae6` (2026-10-31).
This is a source/repository and CI review. It is not a local installation test,
penetration test, provider live test, desktop acceptance test, or independent
security certification.

## Verified baseline

- The repository's current `main` commit has 30 successful GitHub check runs and no failed check runs at the time of this audit.
- The quality-gates workflow runs Python 3.11 tests on Ubuntu, Windows and macOS, installs `requirements.txt`, runs `pip check`, compiles Python sources and discovers the complete unittest suite.
- The repository has provider adapters/factory/catalog code, durable task/workflow primitives, guarded skills, CLI diagnostics and a self-upgrade proposal boundary.
- The README explicitly states that Yuki is not production-ready and that the current bounded JSON model-guided task loop is not a production-grade native tool-calling/ReAct runtime.
- The current CLI `yuki_cli.py` exposes doctor, provider-status, one-shot ask and simple chat. Its chat mode explicitly does not execute tools.
- The tracked tree contains no desktop application implementation/package (no Electron, Tauri, or desktop UI project was found).
- The GitHub repository API reports the repository is public. This must be an explicit product/security decision before personal or customer data is used.

## Release blockers

### P0 — Repository and data exposure decision
- Repository visibility is public. Owner must decide whether to make it private before any private development material, credentials, customer information or personal data are committed.
- Review the complete Git history and GitHub Actions logs for accidentally committed credentials or sensitive data. Revoke and rotate any exposed credential; deleting a current file is not sufficient.
- Keep user databases, memory, recordings, browser profiles, provider keys and logs outside source control.

### P1 — Core assistant functionality
- Complete one supported real-provider conversation path with bounded context, timeouts, cancellation, structured errors, redacted telemetry and explicit model capability reporting.
- Connect model tool calls only to registered tools with schema validation, authorization, risk classification, approval, deadlines and output limits. Model output and retrieved content are untrusted.
- Prove that provider selection, configured credentials, local Ollama and Gemini CLI paths work in documented target environments. Keep live-provider tests opt-in and secrets out of CI.
- Do not market unimplemented Anthropic, DeepSeek, Kimi or MiniMax integrations as available.

### P1 — Desktop and installation
- There is no desktop application project in the current tracked tree. Build a desktop shell only after the shared application/service boundary is stable; do not duplicate model, memory, approval or task logic in UI code.
- Define supported OS/Python versions, clean-install tests, optional dependency groups, model download/storage behavior, upgrades and uninstall/data-retention behavior.
- Portable SSD launchers are not equivalent to a self-contained, zero-install application. Test drive removal, read-only media, path spaces, permissions and recovery.

### P1 — Security and recovery
- Complete a threat model for prompt injection, untrusted skills/plugins, workspace path/symlink escape, SSRF/DNS rebinding, browser downloads, MCP tools, credential storage and local database access.
- Plugin imports execute code with Yuki's permissions; plugin discovery remains opt-in and trusted-module configuration must not be weakened for convenience.
- Validate encrypted backups and actual restore on supported operating systems. Backups must be consistent with SQLite state and must not include secrets in plaintext.
- Add durable audit coverage for approvals and consequential tool calls while redacting prompts, secrets and personal payloads.
- Treat self-upgrade proposals and test execution as untrusted code. Test only in disposable isolation without production credentials or network privileges.

### P1 — Production operations
- Add structured logs, health checks, graceful shutdown, bounded queues, cancellation, rate limits, retry budgets and crash recovery to the actual application composition.
- Define state schema versions, migrations, atomic writes and backup/restore compatibility.
- Publish a support matrix and a release artifact/signing/update policy. Pin or constrain dependencies and review licenses for imported open-source code.

## Phase exit criteria

A production candidate is not approved until all applicable checks are evidenced:

1. Clean install and uninstall/reinstall tested on each supported OS.
2. Full CI green on Ubuntu, Windows and macOS for the exact release commit.
3. One real-provider conversation and one registered-tool workflow tested end-to-end with approval and denial cases.
4. No unauthorized tool execution under adversarial prompt/content tests.
5. Memory/data export, deletion, encrypted backup and restore demonstrated.
6. Provider outage, malformed output, timeout, cancellation, restart and partial failure tested.
7. Desktop package and CLI share the same application/service layer; if desktop is not included in the first release, state that clearly and ship CLI-only.
8. Repository visibility, secret history, dependency/license review and security reporting are resolved.
9. Release notes document implemented, configured, tested, unavailable and unsupported capabilities separately.

## Immediate execution order

1. Resolve repository visibility and secret-history review with the owner.
2. Freeze feature expansion and stabilize the current `main` baseline.
3. Close provider/tool-loop and security/recovery acceptance gaps.
4. Ship a narrowly scoped CLI personal-use candidate only after the acceptance criteria pass.
5. Build and package desktop against the proven shared service layer.
6. Revisit multi-agent scale and cloud hosting after local reliability is demonstrated.

## Branch hygiene

Feature branches based on old phase chains must not be merged automatically. Reconcile useful commits onto current `main` with cherry-picks or a fresh branch, then run the full current-main CI suite. In particular, Phase 107–109 PRs are not assumed to be prerequisites for the newer mainline implementation.
