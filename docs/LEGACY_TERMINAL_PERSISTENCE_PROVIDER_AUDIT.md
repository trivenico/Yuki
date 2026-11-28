# Legacy terminal persistence and provider composition audit

Date: 2026-11-28

## Scope and entry-point distinction

The root-level `yuki.py` module is a compatibility terminal application. It is
not the installed `yuki` command: `pyproject.toml` maps that command to
`yuki_cli:main`. The installed CLI builds the newer conversation runtime and
explicitly reports that local tool execution is disabled. Do not infer that
the legacy terminal's command surface is available through the installed CLI,
or vice versa.

The README describes root-level modules as retained compatibility facades and
warns against bulk moves before import and packaging coverage. Keep the legacy
module and its existing data filenames stable until an explicit compatibility
and migration decision is made.

## Persistence inventory

| Domain | Current path | Write behavior | Assessment |
|---|---|---|---|
| Legacy notes | `yuki_memory.json` beside `yuki.py` | Now uses the shared cross-process JSON lock and durable atomic replacement; writes compare the expected snapshot | Atomicity and stale-writer protection improved; source-checkout parent is not guaranteed private |
| Manual checklist | `yuki_tasks.json` beside `yuki_tasks.py` | Shared file lock, atomic replacement, strict validation and stale-snapshot compare-and-swap | Not an execution queue; keep sensitive descriptions out |
| Learning history | `yuki_learning.json` beside `yuki_learning.py` | Shared file lock and atomic replacement; bounded/redacted records | Loader currently skips malformed records; parent privacy remains unresolved |
| Metadata audit | `yuki_audit.json` beside `yuki_security.py` | Shared file lock and atomic replacement; bounded/redacted metadata | Loader treats unreadable/corrupt content as empty, so a later append can replace corrupt history; parent privacy remains unresolved |
| Durable task queue | `.yuki/durable_tasks.sqlite3` relative to `yuki.py` | `DurableTaskEngine` opens SQLite directly and creates parent directories with default permissions | No canonical profile fence; directory/file privacy is not established by the legacy caller |
| Skill approval records | `.yuki/skill_approvals.sqlite3` relative to `yuki.py` | `SkillApprovalStore` opens SQLite directly | No canonical profile fence; directory/file privacy is not established by the legacy caller |

The JSON lock helper deliberately assumes its parent directory is private.
Its directory helper creates missing components with owner-only permissions
but leaves existing directories unchanged. Consequently, lock-file mode
0600 and JSON-file mode 0600 do not prove that an existing source checkout
parent is private.

## Changes in this audit

Legacy note persistence in `yuki.py` now uses `core.json_file_store`:
- reads are serialized with the same cross-process lock as writes;
- malformed or unreadable data raises a fail-closed error rather than being
  interpreted as an empty memory that can be overwritten;
- writes use the shared fsync + atomic-replace helper;
- command-level add/clear operations compare the loaded snapshot and update
  the in-memory list only after the disk write succeeds.

The legacy `yuki_memory.json` filename and location are intentionally
unchanged. This is a compatibility-preserving integrity improvement, not a
private-profile migration. Tests were added for round-trip persistence,
stale-snapshot rejection, and corrupt-file preservation.

## Provider configuration and lifecycle

The legacy `ProviderManager` takes an optional in-memory config mapping and
otherwise reads `YUKI_PROVIDER` from the environment. Provider selection via
`/provider select` changes the live manager/agent instance; the inspected
legacy flow does not persist that selection. The provider factory receives
environment-backed credentials; no API key is written into the legacy JSON
memory, learning, task, or audit files by this code path.

The legacy terminal's provider manager has an explicit close/lifecycle API,
but `yuki.py:main()` does not currently close it in a `finally` block. The
process exit eventually reclaims OS resources, but orderly adapter shutdown
is missing. This is a cleanup issue distinct from provider credential storage.

## Execution and reachability warning

Unlike the installed conversation-only CLI, root-level `yuki.py` exposes
`/skill invoke`, `/tool`, and `/durable run`. The durable command creates
a `DurableSkillWorker` and can invoke registered skill implementations
through the approved skill runtime. This audit does not certify that legacy
execution surface as production-safe. Its durable task and skill-approval
databases bypass the canonical maintenance fence and the legacy root does not
establish an owner-private profile before opening them. Do not use this
entrypoint for consequential or unattended actions until its authorization,
private-profile, maintenance-fence, lifecycle, isolation, and recovery
contracts are reviewed together.

## Retirement and migration decision

- Do not retire `yuki.py` or delete its data files in this pass. It is an
  importable compatibility module with existing tests and distinct command
  behavior.
- Do not silently move any legacy file into `YUKI_HOME` or `.yuki/private-profile`.
  A future migration needs an owner-visible plan, a quiesced source, a verified
  backup, strict schema validation, collision handling, atomic publication,
  and rollback/recovery instructions.
- Do not treat the manual JSON checklist as a worker queue.
- Before reusing the legacy SQLite queue/approval stores, require a validated
  private profile, one shared maintenance fence, owner/session-bound approval,
  bounded worker lifecycle, and explicit uncertain-outcome reconciliation.
- The installed `yuki` command remains the preferred current CLI path; the
  legacy terminal module should be considered compatibility/experimental until
  the execution surface is either retired deliberately or brought under the
  canonical control plane.

## Validation boundary

Tests are authored but were not run locally. No legacy data was read, migrated,
deleted, or rewritten by this audit. No worker was started, no skill was
invoked, no provider request was sent, and no deployment or merge occurred.
