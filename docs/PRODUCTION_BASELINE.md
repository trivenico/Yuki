# Yuki Production Baseline

**Audit date:** 2026-10-20  
**Audited ref:** `phase-production-hardening-ci`  
**Status:** Prototype / active hardening. This document is the required read-first baseline; findings are observations, not a production-readiness certification.

## Scope and method

Read the primary runtime and required modules: `main.py`, `core/planner.py`, `core/vector_memory.py`, `skills/registry.py`, README capability claims, the knowledge indexer/graph, voice STT/TTS/wake-word adapters, firewall/IDS, Docker sandbox/Nmap recon, configuration loader, current CI workflow, representative integration tests, and requirement files. Checked the current GitHub Actions logs for the hardening branch.

This was a repository-source and CI-log audit through GitHub. No clean local clone/venv was available in this audit, so package installation, static-analysis tools, coverage instrumentation, and benchmark timings have **not** been run. Repository-wide textual search through the connected GitHub search endpoint returned no results for common TODO/FIXME/XXX/HACK terms; this endpoint result is not proof that those markers are absent from every file.

## Current test coverage

- Test framework: Python `unittest`, discovered with `python -m unittest discover -s tests -v`; pytest migration is not done.
- Latest inspected CI run for commit `d0cd5970ecd00c1434a73056f7da7571cd8ec8b0`: Python 3.10 and 3.12 each ran 162 tests but failed at test discovery because `tests/test_approval_flow.py` imports the absent `core.approval_flow`. Python 3.11 was still running at inspection time. Do not treat this run as green.
- The current workflow compiles sources and runs unittest on Python 3.10, 3.11, and 3.12. It does not yet enforce pytest coverage, Ruff, mypy, pip-audit, Bandit, or an 80% coverage threshold.
- Existing tests cover meaningful slices of permissions/approval behavior, provider selection, configuration validation, local memory CRUD, planner proposal bounds, workflow orchestration, selected security guards, knowledge graph/export, and optional integration fail-closed behavior. Coverage percentage has not been measured; no defensible numeric coverage claim can be made.
- Blocking suite inconsistency: `tests/test_approval_flow.py` expects a manifest/dispatcher/subprocess-execution architecture (`core.approval_flow`, `core.approval_audit`, `core.manifest_task_agent`, `core.skill_dispatch`, `core.skill_manifest`, `core.subprocess_executor`) that is absent from the audited branch. Existing `YukiCoreAgent` uses a registry and permission gate and does not expose the test's dispatcher constructor. Reconcile the intended design and implementation; do not mask this with placeholder modules or by deleting tests.

## TODO / FIXME inventory

- GitHub indexed search for `TODO FIXME XXX HACK` returned no matches, but repository-wide inventory is **unverified** because the search endpoint may not index all content. Phase 1 must run a local exhaustive scan over tracked source/docs and record all matches, including comments and markers such as `NotImplemented`.
- Explicit known incomplete features documented by README/runtime: production-grade native tool calling/ReAct is absent; autonomous project builder/editing is disabled; custom wake-word model training is not provided; full wake-word background listener is absent; firewall live-rule expiry/rollback/recovery is not implemented; Metasploit execution is not implemented; speaker verification is a fail-closed placeholder; end-to-end conversational voice reply and barge-in are not wired; token usage metrics are not connected to actual provider usage; MCP is not wired into runtime command/registry/UI; automatic knowledge “dream cycle” is absent; course-coach tutor/assessment execution is a scaffold.
- These are documented gaps rather than TODO comments. Phase 7 must keep claims aligned with code and tests.

## Dead code and duplicate utility candidates

- No claim of a complete dead-code report can be made without local static analysis, import graph inspection, and tests. This is a Phase 1 task, and removal must be test-backed.
- `main.py` imports and initializes multiple subsystems directly (voice, security, sandbox, firewall, knowledge, reminders, evolution, providers, agents). The CLI/runtime has significant wiring in one module; consider extracting construction/command handling only after baseline tests characterize behavior.
- `core/vector_memory.py` is named “VectorMemory” but implements bounded lexical token-overlap ranking, not vector embeddings or a vector database. README currently discloses this accurately.
- `core/planner.py` emits a bounded static proposal and has no execution method. Keep proposal and execution separate.
- Multiple JSON-backed persistence helpers exist (memory, reminders, evolution/identity, graph). Atomic-write, recovery, schema-version and common-helper reuse need review before consolidating them; do not collapse utilities solely on naming similarity.

## README claims not fully supported or explicitly experimental

The README is comparatively candid and says the project is not production-ready. Still, verify these claims with automated tests and clarify any missing evidence in Phase 7:
- “every registered-skill invocation” requires fresh approval: current guarded agent path is approval-gated, but approval-failure/timeout proof is not yet present for every registered skill and every invocation path.
- “optional integrations fail safely” needs per-integration tests and dependency-missing coverage.
- The Docker sandbox documents several controls (digest allow-list, network disabled, read-only root, dropped capabilities, resource limits, explicit approval); the runtime command also mounts the workspace read-write, has no seccomp profile explicitly configured, and output is truncated after process completion rather than bounded while being collected. Reconcile docs/controls and add tests.
- Wake-word detection is a finite, explicit local adapter, not trained or always-on; the configuration gate is currently named `YUKI_WAKEWORD_ENABLED`, not the mission-requested `YUKI_VOICE_WAKEWORD_ENABLED`.
- Firewall behavior is simulation/dry-run by default; live mutation expiry and rollback are absent.
- Gmail is read-only and optional; token refresh, revocation, keyring storage, and minimal-scope review remain security work.
- Voice STT explicitly writes WAV files when requested; a global “never write audio without a flag” policy is not yet demonstrated.
- README describes the install as Python 3.10+ with PyYAML and requirements files; there is no `pyproject.toml`, console entry point, or PEP 621 package metadata on the audited branch.
- No Prometheus endpoint, structured rotating JSON log setup, complete audit reconstruction, encrypted-at-rest stores, portable export/wipe round trip, nightly backup/rotation, or full operational/release document set is established by the inspected files. These remain mission requirements, not supported claims.

## Dependency and supply-chain risks

- Runtime requirements list `PyYAML>=6.0.2,<7.0`; optional requirement files use compatible-version ranges rather than exact pinned versions with hashes. There is no lockfile/constraints file evidenced in this audit.
- Voice packages (faster-whisper, sounddevice, NumPy, openwakeword, soundfile, pyttsx3), UI (Textual), Gmail OAuth/API clients, and Scapy add native binaries, model downloads, platform dependencies, and/or supply-chain surface. Several optional dependencies are not needed for a minimal local core.
- No pip-audit, Bandit, Semgrep, SBOM, or dependency provenance report was available in the inspected CI workflow.
- Faster-whisper may download model weights on first use; this is an optional feature and conflicts with strict no-network test assumptions unless mocked or pre-provisioned.
- Phase 1 should mirror supported requirement groups in optional extras and justify/pin new tooling/dependencies as requested. Phase 2 should run no-network tests and enforce scanner/coverage gates.

## High-priority baseline gaps

1. **P0 — Tests currently fail** due to stale/missing approval-flow architecture imports. Resolve design mismatch first; do not proceed to later phases while suite is red.
2. **P0 — Approval safety proof**: add tests that a missing, false, raising, or timing-out approval callback refuses execution for every registered skill. Verify each skill invocation requests a fresh decision. Preserve planner proposal-only behavior.
3. **P1 — Packaging/tooling**: no `pyproject.toml`, console script, Ruff/mypy/pre-commit configuration, or package installation verification.
4. **P1 — Test quality/CI**: migrate to pytest; mock provider/network boundaries; establish real coverage and scanner gates.
5. **P1 — Resilience**: provider timeout/retry/circuit breaker, bounded subprocess output, process cleanup, shutdown journaling, and session recovery are not comprehensively demonstrated.
6. **P1 — Audit/observability**: no proven append-only approval+skill audit trail, structured redacted rotating logs, complete /status health inventory, or opt-in metrics gate.
7. **P1 — Security/config/privacy**: unknown configuration keys need a rejection test; secret keyring flow, web-fetch SSRF/DNS-rebinding defense, untrusted-content handling, per-skill rate limits, explicit destructive-intent confirmation, and full threat model need validation.
8. **P1 — Data integrity**: SQLite schema/migrations/WAL, encrypted-at-rest storage, backup rotation, verified wipe and export/restore round-trip are not established.
9. **P2 — Release operations**: no verified multi-stage production Docker/compose/systemd packaging, SemVer release workflow, or complete installation/configuration/runbook/security/contributor docs.

## Phase policy

- Work only on the designated hardening branch.
- After each phase, run its full required test suite, commit with a conventional commit, and report changed behavior, coverage, and residual risks.
- Do not start the next phase with failing tests. A failed or unavailable validation run is reported as blocked, never passed.
- Preserve local-first defaults, no secrets in source/logs/tests/docs, no telemetry without explicit opt-in, and fail-closed approval on all error paths.

## P0/P1 follow-up audit — 2026-10-20

### Confirmed source facts

- `config.yaml` defaults `provider: mock`. The repository also contains Ollama and OpenAI-compatible text-generation adapters and a bounded JSON task loop in `core/task_agent.py`; this is not evidence of a production-grade native tool-calling runtime or a verified end-to-end agent.
- `README.md` already labels Yuki not production-ready and discloses that the model-guided task loop is bounded and not a production-grade native tool-calling/ReAct runtime.
- `SECURITY.md` now more precisely distinguishes provider adapters from verified live behavior and says a private reporting channel must be explicitly configured by repository owners.
- A new `.github/workflows/security.yml` adds dependency auditing with pinned `pip-audit`, Bandit static analysis, and Gitleaks history scanning. Its results must be reviewed; adding a workflow is not evidence that the scans passed.
- The expected backup implementation was not found at `core/backup.py`, `core/backup_manager.py`, `automation/backup.py`, `scripts/backup.py`, or repository-root `backup.py` on the audited branch. This is not an exhaustive inventory of every possible backup/archive path. Do not claim encrypted backup support until the actual implementation is located, integrated, and restore-tested.
- `docs/PORTABLE_SSD.md` recommends full-drive encryption, but that is a user-operational mitigation, not application-level authenticated encryption of backup archives.
- The previously observed Phase 1 CI blocker remains Ruff lint failures. Do not advance the phase gate until lint, formatting, type checking, package install/entrypoint smoke test, and the full test matrix pass on the same commit.

### Immediate release blockers

1. Locate every backup/export/archive path using a complete tracked-file inventory; document exactly which private data is included.
2. Implement authenticated encryption with externally managed keys and no plaintext temporary archive; add wrong-key/tamper rejection, size-bound, and restore round-trip tests.
3. Review Gitleaks findings across full history and rotate exposed credentials before considering history rewriting.
4. Review security workflow results and remediate findings rather than disabling checks to obtain a green build.
5. Keep the repository's public capability claims aligned with the default mock provider and the verified runtime path.

### Security workflow triage — 2026-10-20

The first security workflow run completed with these outcomes: Gitleaks secret-history scan passed; `pip-audit` passed for installed runtime dependencies; Bandit failed on four medium-severity findings (three uses of `urllib.request.urlopen` in provider adapters and the sandbox's container-only `/tmp` tmpfs).

Remediation committed on the hardening branch:
- Added `providers/http_client.py` with a redirect-rejecting HTTP opener so model-provider requests cannot follow redirects that could move prompts or bearer authorization headers to a different endpoint.
- Routed Ollama and OpenAI-compatible adapter requests through the helper.
- Documented the Bandit B108 exception on the Docker container's ephemeral `/tmp` tmpfs mount; it is inside the container and uses `noexec,nosuid`.
- Added unit tests for rejecting both cross-host and same-host redirects.

**Not yet verified:** the post-fix CI/security workflow results. The prior CI run still fails at Ruff before format, mypy, entry-point smoke test, or unit tests execute. Gitleaks passing means that scan found no findings in that run; it does not prove all secrets are absent or that private data never entered history.

### Follow-up provider-boundary regression coverage — 2026-10-20

Added `tests/test_provider_security.py` to cover:
- rejection of plain HTTP for non-loopback Ollama endpoints;
- rejection of URL-embedded credentials;
- rejection of non-HTTPS OpenAI-compatible endpoints;
- a real loopback-only HTTP redirect test proving the provider helper does not follow the redirect.

The tests do not call external services. They are committed but not yet confirmed passing in CI. They complement the unit tests for same-host and cross-host redirect handler behavior; they do not establish complete SSRF protection, DNS-rebinding resistance, or a network-level egress firewall.

Latest commit observed: `d533fd0d16ef43e1a39b9888f9132f76a9ecb463`. CI, Security, and Yuki tests workflows were queued when checked; no passing result is claimed.

### CI failure triage and corrective commits — 2026-10-20

Inspected the workflow results for commit `d533fd0d16ef43e1a39b9888f9132f76a9ecb463`:

- Unit tests: **failed** (179 tests run, 1 failure, 2 errors, 5 skipped). The three provider tests were patching `urllib.request.urlopen`, but production code now calls `open_provider_request`; therefore their mocks no longer intercepted the requests. The tests were updated to patch the actual transport seam.
- Security: Bandit and Gitleaks passed. `pip-audit` failed because the installed optional backup dependency resolved to `cryptography 46.0.7`, which had four advisories in that run, and because the local editable project was not a PyPI project. The backup extra's cryptography floor was raised to `50.0.0` (the highest minimum fix version reported), and the audit command now uses `--skip-editable` so it audits third-party distributions rather than the local project.
- Quality CI: Ruff still fails with 231 findings; formatting, mypy, entrypoint smoke test, and CI's unit-test step were skipped. The current workflow logs include extensive E701/E702 style violations and unused imports. These are not waived.

Corrective commits:
- `a334bb2eef9b2787f2942d07cc64a4aff32a8e34`: adjust provider unit-test mocks to use the new HTTP helper.
- `135627911cbccd5d8cb1b158e20934a212e23d52`: finish switching provider tests to patch the transport seam.
- `4d5f5744624ebed5ef2bca3541f9ac3621dd62bf`: raise cryptography version floor for the optional backup extra.
- `02badf911fb87da3a890aa8db6cfa7965cc51fb3`: skip the editable local project in pip-audit.

**Verification status:** these corrective commits have not yet been confirmed by a fresh passing CI/security run. Do not treat the version-floor change or mock correction as verified until the workflows run successfully.

### Backup encryption implementation increment — 2026-10-20

Added `core/backup_encryption.py` with AES-256-GCM authenticated encryption for an already-created archive, a versioned magic header, bounded input reads, rejection of symlink inputs/destinations, same-file protection, atomic destination replacement, mode `0600`, and authentication failure before writing decrypted output. The helper accepts a 32-byte key from its caller and intentionally does not persist keys or create the archive itself.

Added `tests/test_backup_encryption.py` covering round-trip, ciphertext not containing the test plaintext, wrong-key rejection, tamper rejection, invalid key, size limit, same-file rejection, and symlink input rejection.

**Important limitations:** this is a focused encryption primitive, not a complete backup/restore workflow. It reads the archive into memory up to the configured limit (default 512 MiB), does not inventory/select sensitive data, does not source keys from a secret manager, and does not prevent callers from creating an unencrypted archive before invoking it. Integration into backup/export paths and restore testing remain required. Tests are committed but no passing CI result is confirmed yet.

### DarkChocolate prototype increment — 2026-10-20

Added an explicitly scoped prototype under `dark_chocolate/`, separate from Yuki's production-hardening baseline. It includes local Ollama chat, a bounded ReAct loop, heuristic prompt inspection, a plan approval lifecycle, a central risk/access-aware tool registry, JSONL audit logging with limited redaction, bounded working memory, a JSON knowledge graph with Obsidian export, mood prompt injection, a workspace-scoped report writer, setup scripts, and initial tests.

Security correction: the default ReAct access level is `unknown`, not `owner`, so tool calls requiring owner access fail closed until real authentication is integrated. Ollama endpoints are restricted to exact loopback hostnames over HTTP to avoid a hostname-prefix SSRF bypass.

**This is not the full requested DarkChocolate system.** Face recognition, egress firewall, real sandboxing, MCP, browser/email, pentest tools, IDS/firewall integrations, ChromaDB, durable queue/daemon, voice, kill switch, and evolution remain unimplemented. The workflow is a one-step proposal, not an LLM-generated multi-step plan. The current app does not load all settings from YAML. The audit redaction is best-effort and not a guarantee against all secret leakage.

No passing CI run has been confirmed for these new files. Run the targeted unit tests and the full repository gates before treating this increment as verified.
