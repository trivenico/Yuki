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
