# Yuki repository audit — 2026-11-09

## Scope and method

This is a staged engineering audit of the feature branch `feature/reasoning-context-builder`. The complete Git tree was inventoried at the start of the review: 991 tracked files, including 631 Python files, 281 test files, 290 Markdown documents, and 26 GitHub Actions workflows. No committed database files, compiled Python bytecode, or obvious binary build artifacts were found in that tree.

The first pass reviewed the project layout, dependency manifests, CI workflows, configuration and provider construction, browser ASGI composition, authentication boundaries, memory/retrieval context construction, MCP/plugin and skill-pack boundaries, optional voice input, and backup/restore workflow. The fixes below are backed by regression tests where practical. This document does not claim that every line in all 631 Python files has been manually reviewed; the audit is intentionally iterative so high-risk defects are fixed and verified before broader module-by-module review.

## Confirmed defects fixed in this pass

1. **OpenAI model configuration was silently ignored.** `config.yaml` declared `openai_model`, but `load_config()` did not return it, so the provider factory fell back to its default model. Configuration now validates and returns the setting, honours `YUKI_OPENAI_MODEL`, and has regression tests.
2. **Provider default inconsistency.** The configuration loader defaulted to the test-only `mock` provider while the provider factory and shipped configuration defaulted to OpenAI. A minimal configuration could therefore fail during runtime construction. The loader now defaults to the real OpenAI adapter.
3. **Skill-pack installation race.** Publishing with `os.replace()` could overwrite a pack created concurrently after the existence check. Publication now uses atomic no-overwrite hard-link creation and a regression test simulates a competing installer.
4. **Skill-pack removal followed symlinks.** Resolving the destination before unlinking could delete the referent of an in-directory symlink instead of the named entry. Removal now refuses symlinked pack entries; a regression test verifies the referent is preserved.
5. **Wake-word audio allocation was not fully bounded.** `listen_once()` bounded duration but did not validate sample rate, allowing callers to request an unexpectedly large recording buffer. It now accepts only the supported fixed sample rates and rejects non-integer values before importing audio dependencies.
6. **Real-provider acceptance runtime injection mutated module globals.** The browser launcher now supports explicit configuration/provider dependency injection, removing the need for process-global monkeypatching in the acceptance command.
7. **Acceptance ASGI requests used an incomplete scope and assumed a single response-body event.** The helper now supplies a complete HTTP ASGI scope, includes content length, collects bounded response-body chunks, and validates JSON object responses. Both requests run on one event loop, matching normal ASGI server lifecycle more closely.
8. **Environment example contained a duplicate setting.** Removed the repeated `YUKI_SANDBOX_ENABLED` line.

9. **MCP DNS rebinding / proxy bypass risk.** Endpoint validation previously resolved and checked the host, then delegated the connection to urllib, which could resolve the name again or honour an ambient HTTPS proxy. The client now pins the TCP destination to a validated public IP, preserves the original hostname for TLS certificate verification/SNI, and disables environment proxy discovery. Unit tests cover mixed public/private DNS answers, IP pinning, and proxy bypass resistance.
11. **Cloud provider outbound DNS trust gap.** The OpenAI-compatible adapter allow-listed hostnames and rejected redirects but previously delegated DNS and proxy selection to urllib. It now resolves the allow-listed provider hostname at request time, rejects any private/non-public DNS answer, pins the TCP destination while retaining TLS hostname verification, and disables environment proxies. Provider constructors remain network-free; tests inject a deterministic resolver.
12. **Ollama ambient proxy leakage.** Ollama was documented as a local/configured provider but used urllib's environment-aware default opener for health checks, generation, and chat. A service-level HTTP(S)_PROXY could redirect local prompts through a third party. All Ollama HTTP paths now use an explicit empty proxy map and reject redirects.
13. **Embedding numeric overflow/underflow.** Retrieval validated finite Python floats but did not bound magnitudes before cosine scoring or float32 cache serialization. Very large integers could raise raw overflow errors, and tiny nonzero values could serialize to an all-zero vector. Retrieval and the SQLite index now enforce a bounded component range, reject zero/underflowed vectors, and reject corrupted out-of-range cache values.
14. The same numeric guard is applied at the local Hugging Face embedder boundary and the older hybrid semantic retriever, so invalid vectors are rejected before cache serialization or cosine scoring regardless of which retrieval stack is configured.
10. **Verification process-tree leakage.** Local command timeout/output-cap handling previously killed only the direct child, potentially leaving descendants alive. Local verification now starts a dedicated process group/session and terminates the group on timeout/output overflow. Docker verification now assigns a unique container name and explicitly removes the daemon-side container on timeout/output overflow; killing the Docker CLI alone was not sufficient.

15. **Docker snapshot memory exhaustion.** The verification executor previously read every workspace file into memory before applying the snapshot builder's limits. It now checks file count, per-file size and total size before allocation, uses bounded reads with race checks, and skips the builder's excluded cache/VCS directories before reading their contents. A regression test covers an oversized file.

## Architecture and release observations

- The repository has no standard `pyproject.toml` or equivalent project-level lint/type-check configuration. Current core CI compiles Python and runs unittest on Python 3.11 across Ubuntu, Windows, and macOS; this is valuable but does not replace linting, type checking, coverage policy, or multi-version compatibility testing.
- Some modules are exceptionally large (for example, `automation/agent_coordinator.py`, `automation/workflow_store.py`, `main.py`, and `api/conversations.py`). Size alone is not a defect, but these modules carry higher change and review risk. Refactoring should follow domain boundaries and preserve compatibility, not be done as a mechanical split.
- The web profile deliberately denies tools by default. Public deployment still needs deployment-specific TLS/proxy, host/origin, login rate-limit, network, secret-management, database, and browser security validation. The repository's `SECURITY.md` correctly labels the application early-stage and not production-ready.
- The live-provider acceptance command now covers the authenticated browser ASGI route with an opt-in real provider, but it is not a TCP deployment test, a production inference reliability benchmark, or a process-restart memory acceptance test.
- MCP endpoints are allow-listed and HTTPS-only, redirects are rejected, and resolved non-public addresses are rejected. DNS resolution and the subsequent HTTP connection are separate operations; pinning the validated address to the TLS connection remains a worthwhile SSRF-hardening review item.
- Optional voice, browser, embedding, security, and UI dependencies are separated into additional requirement manifests. The default CI primarily validates the base dependency set; optional-feature installation/acceptance coverage should be expanded.

## Verification policy

A change is considered verified only when the corresponding regression test and the current branch's relevant CI jobs pass. Queued, cancelled, or superseded workflow runs are not counted as passing. No real-provider success is claimed unless the opt-in command has been run against a configured OpenAI or Ollama endpoint.

## Remaining audit queue

1. Continue reviewing MCP outbound policy and adversarial DNS/redirect/TLS edge cases.
2. Extend process-tree cleanup review to all subprocess launchers and verify Windows Job Object semantics.
3. Audit every SQLite store for transaction ownership, connection closure, migration atomicity, and multi-process contention.
4. Audit authentication throttling, session lifecycle, public signup policy, and deployment trust-proxy assumptions.
5. Review provider response limits, retry/backoff, timeout propagation, cancellation, token accounting, and model capability declarations.
6. Review memory lifecycle, retrieval ranking, embedding cache invalidation, cross-owner isolation, and prompt-injection evidence boundaries.
7. Review desktop Rust/Tauri permissions, frontend request handling, packaging/signing, and updater trust.
8. Add a maintainable lint/type-check baseline and test optional dependency profiles without weakening existing platform coverage.
9. Continue module-by-module review of the remaining Python packages and tests; refactor only where a concrete defect or measurable maintainability issue is demonstrated.
