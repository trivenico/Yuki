# Phase 223 — durable workspace transaction journal

## Status

Engineering implementation in progress. This is not a production certification.

## Transaction protocol

The workspace executor now writes a private journal outside the editable workspace before the first target replacement. Each operation records a random operation ID, actor, operation digest, exact relative target, old/new SHA-256 digests, original mode, durable backup and same-directory staged replacement.

The journal uses atomic manifest replacement, file flush + fsync, and directory fsync on POSIX. The transaction state is:

- `prepared`: all backups and staged files exist; no target replacement is permitted yet.
- `applying`: replacements may begin; each completed replacement is recorded.
- `committed`: every target matches its approved new digest. Recovery must never roll this state back.
- `rolled_back`: all targets have been restored to their original digests.
- `recovery_required`: recovery found an integrity or filesystem conflict and stopped without overwriting unknown content.

A crash between a target rename and its journal update is reconciled by comparing the target against the recorded old and new digests. Recovery restores only files matching the approved new digest, leaves files already matching the old digest untouched, and refuses to overwrite any third-party content.

## Authorization and fencing

Normal execution validates the live durable worker claim before each replacement. Recovery is a compensating action and intentionally does not depend on that potentially expired claim. It requires a trusted principal with the separate `workspace:recover` permission.

The journal does not make multiple filesystem renames atomically visible. Readers may observe an intermediate state while a multi-file transaction is applying. It guarantees a durable recovery decision, not true multi-file atomic visibility.

The existing legacy `approve_change` callback remains an additional local authorization gate after the central typed approval has been consumed. This is fail-closed defense in depth, but the approval contracts are not yet unified; a deployment must not configure the callback to mint or accept unrelated approvals. A dedicated typed receipt contract remains required.

## Current limitations / release blockers

- No cross-process transaction lock or serializable multi-writer coordination yet. Concurrent writers must be prevented by deployment topology until a fenced lock is implemented and tested.
- A crash during journal preparation, before the prepared manifest is durable, can leave orphan staging files; no target file has been replaced at that point. Automated orphan-stage cleanup is not yet implemented.
- Directory fsync is performed on POSIX. Python does not provide an equivalent portable directory-fsync contract on Windows; Windows power-loss durability needs platform-specific validation.
- No subprocess kill / power-loss simulation across Windows, macOS and Linux yet.
- Journal retention, quota/cleanup policy, operator alerting, and recovery supervisor integration remain outstanding.
- The default journal location is a private per-workspace directory under the system temporary directory. Production deployment should supply a dedicated protected state volume and validate its ownership, ACLs, backup and encryption policy.
- The test environment uses injected callbacks; production owner authentication, audit durability and key management remain separate release gates.

## Verification

The added tests cover interrupted replacement recovery, idempotent repeated recovery, refusal to overwrite conflicting external content, backup tampering, committed-operation preservation, authorization for recovery, and audit failure after durable commit. CI must pass on the exact PR head before this phase can advance.
