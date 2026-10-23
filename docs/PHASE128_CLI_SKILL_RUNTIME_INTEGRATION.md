# Phase 128 — CLI skill runtime integration

Adds /skill commands to the terminal entry point and routes invocation through
ApprovedSkillRuntime / invoke_registered_skill, rather than calling skill.execute()
from the command handler.

## Commands

- /skill list — list registered built-in skills with risk and description.
- /skill approve NAME — grant a process-local one-shot approval for a registered skill.
- /skill invoke NAME [argument] — invoke the named skill through the guard.

One-shot approvals are stored in process memory and consumed by the guarded runtime.
Restarting Yuki clears pending approvals. Approval is not persisted and does not
survive restarts. The SQLite store created under .yuki/skill_approvals.sqlite3
is reserved for manifest registration approvals and is not used as a blanket
invocation grant.

## Security notes

- Unknown skills are rejected by the runtime.
- High/critical risk or skills declaring requires_approval need one-shot approval.
- Audit events record action and outcome metadata, not arguments or results.
- The command handler constructs the built-in registry; it does not import
  arbitrary discovered manifest entrypoints.
- Existing /tool command is unchanged in this phase; direct tool dispatch still
  needs separate guard integration.
- This does not wire arbitrary third-party skills, planner agents, or worker
  execution paths. It is the first user-facing CLI path for guarded registered
  skills, not proof of a single repository-wide execution boundary.

## Manual verification

    /skill list
    /skill invoke system
    /skill approve system
    /skill invoke system

Use an actual registered skill name and its expected argument. High-risk actions
may have additional approval or configuration requirements.

## Automated verification

    python -m unittest tests.test_approved_skill_runtime tests.test_approved_skill_wiring -v
    python -m unittest discover -s tests -v