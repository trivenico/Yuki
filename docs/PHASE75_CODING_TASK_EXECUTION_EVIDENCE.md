# Phase 75 — Approved coding-task execution and evidence

This phase connects the durable task record to the isolated Docker runner without automating patch application or task completion.

- Task state must be approved, with an intact reviewed patch.
- Execution is bound to the exact workspace stored for the task.
- Executables are allowlisted; defaults are Python, pytest, Ruff, and mypy.
- The trusted approval adapter receives task ID, patch digest, and exact execution request, enabling actor/session, freshness, single-use, and operation-binding checks.
- Evidence persists task and patch identifiers, command/output digests, byte count, exit code, timeout/truncation flags, duration, and timestamp. Raw output and command arguments are not persisted.
- No patch application, git writes, remote PR creation, or task completion occurs here.

Production follow-ups: wire the adapter to the authenticated approval coordinator; configure digest-pinned images and Docker permissions; add isolated real-Docker CI, cleanup monitoring, evidence backup/retention, and end-to-end approval tests.
