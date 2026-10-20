# Phase 48 — Durable runtime composition

## Audit findings

The SQLite approval store and dispatcher existed, but `main.build_agent()` did not instantiate or inject them. Therefore the live CLI composition did not actually use the durable manifest-dispatch path. Approval database permissions also needed hardening, and symlink rejection had to happen before SQLite opened the path.

## Changes

- Added `core/runtime_composition.py` as the explicit dispatcher composition helper.
- `main.build_agent()` now injects a `SkillDispatcher` backed by `SQLiteActionApprovalStore`.
- Development/test composition registers only the reviewed `system.time.read@1.0.0` contract.
- Production composition uses durable SQLite storage but registers no executable skills; Phase 44/45 fail-closed guards remain in place.
- Approval data lives under `.yuki-security/` with POSIX directory mode 0700 and database mode 0600.
- Reject database symlinks before opening SQLite.
- Added composition, permission, and symlink regression tests.

## Limitations

- This is not a production execution enablement.
- OS sandboxing is still incomplete; Windows and macOS adapters are not implemented.
- Approval creation and consumption are durable, but the broader coordinator and dispatcher lifecycle are not one atomic transaction.
- Validate the SQLite deployment location, backups, and filesystem semantics on each supported OS.
