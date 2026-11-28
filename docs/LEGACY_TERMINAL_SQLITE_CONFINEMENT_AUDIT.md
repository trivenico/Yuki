# Legacy terminal SQLite store reachability and confinement audit

Date: 2026-11-28
Branch: `research/reference-projects-integration-assessment`

## Entry point and reachable operations

The root-level `yuki.py` is an executable compatibility terminal distinct from
the installed conversation-only CLI. Its command dispatcher exposes:

- `/durable goal create`, `task submit`, `task list`, `task cancel`, and
  `/durable run`;
- `/skill approve` and `/skill invoke`.

The durable worker path constructs `DurableTaskEngine` and
`ApprovedSkillRuntime` directly. The skill runtime constructs
`SkillApprovalStore` directly. These are reachable from the legacy terminal,
not merely orphaned store classes.

## Exact persistence and path behavior

- `_DURABLE_TASK_DB` is source-relative: `.yuki/durable_tasks.sqlite3`.
- `_SKILL_APPROVAL_DB` is source-relative: `.yuki/skill_approvals.sqlite3`.
- Both getters call `Path.mkdir(parents=True, exist_ok=True)` on the parent.
  They do not validate ownership/mode, reject symlinked path components, or
  establish the canonical private profile.
- `DurableTaskEngine` creates its parent and opens SQLite directly. It has no
  injected `MaintenanceFence`; its per-operation connections are outside the
  canonical lock authority.
- `SkillApprovalStore` opens SQLite directly and creates its schema. It has
  no private-profile validation or maintenance-fence injection.
- The durable engine's task lease and one-attempt policy help prevent automatic
  replay after an uncertain outcome, but they do not authenticate the terminal
  operator or bind dispatch to the canonical owner session.
- Skill registration approval and in-memory one-shot invocation approval are
  distinct from authenticated owner/session authorization. The legacy command
  surface itself does not establish that session boundary.

## Why this pass does not relocate or silently chmod the stores

Existing databases may contain task history and skill approvals. Moving them
into `YUKI_HOME/.yuki/private-profile`, changing their schema, or tightening
permissions in place can alter compatibility and may affect another process
that still uses the old path. The repository does not provide a complete,
verified inventory of external writers or a quiescence protocol for these
legacy files.

A safe future migration must be owner-visible and explicit: identify every
writer and reader, stop the legacy host, validate source/destination and
sidecars without following symlinks, make a verified SQLite backup, preserve
the old files until cutover is verified, validate imported schemas and rows,
and provide rollback. Permission repair must also be explicit and report what
was changed; do not silently repair an existing directory.

## Decision

No database path, schema, permissions, or runtime authorization behavior was
changed in this audit. Do not use the legacy `/durable run` or skill commands
for consequential or unattended actions. Keep the legacy terminal in
compatibility/experimental status until it is retired deliberately or its
stores, owner/session authorization, fence participation, and worker lifecycle
are migrated and tested as one composition.

No database was opened, created, migrated, or modified by this source review.
Tests were not run locally. No worker or skill was invoked; no merge or
deployment occurred.
