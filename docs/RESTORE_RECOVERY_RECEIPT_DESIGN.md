# Restore recovery receipt: implementation contract

Status: POSIX restore orchestration is wired to sidecar receipts; focused helper and restore integration tests are authored. CI verification remains pending.

## Problem

restore_encrypted_backup() validates and fsyncs an isolated staging tree, optionally installs a portable memory key, renames the stage to the destination, then fsyncs the destination parent. If that final fsync fails, the destination may already be visible and the imported key may already be usable. The current exception text is lost if the process exits, and a blind retry is unsafe because the destination is no longer empty.

## Required operator-visible outcomes

A restore attempt must be inspectable after process restart and must never imply more durability than has actually been confirmed.

| State | Meaning | Safe default action |
| --- | --- | --- |
| PREPARED | All archive validation and staging completed; publication has not been confirmed | Inspect destination and receipt; do not retry automatically |
| PUBLISHED_UNCONFIRMED | Destination rename was observed, but parent-directory durability has not been confirmed | Preserve destination and keyring; verify manually; never overwrite/retry |
| COMMITTED | Destination publication and receipt update were fsynced successfully | Treat restore as completed |
| NOT_PUBLISHED | Publication did not occur and any newly imported key was rolled back | A new attempt may be started after confirming destination is absent |
| RECOVERY_REQUIRED | Keyring rollback or receipt update failed; outcome needs owner reconciliation | Preserve all artifacts and keyring entries |

A missing receipt is not proof that no publication occurred. A corrupt, symlinked, hard-linked, or path-mismatched receipt must fail closed.

## Journal placement and protocol

- Use a unique operation ID and a sidecar receipt in the destination's parent directory, not inside the staged profile. The receipt must survive independently of the profile tree and must not be included in ordinary profile backups.
- The sidecar filename must be derived from a cryptographically random operation ID; create it exclusively (O_CREAT | O_EXCL, no symlink following where supported), as a regular single-link file with owner-only permissions.
- The receipt contains only schema version, operation ID, destination basename, state, creation/update timestamps, and non-secret inventory metadata (file count and byte count). Never store passphrases, encryption keys, keyring values, archive contents, or absolute host paths.
- Persist PREPARED by writing and fsyncing the receipt file, then fsyncing the parent directory before the destination rename. If this cannot be confirmed, do not publish.
- After rename, set the in-memory outcome to published immediately. If parent-directory fsync fails, raise a typed error carrying operation_id, destination, receipt_path, and outcome=PUBLISHED_UNCONFIRMED; retain both destination and imported key. Do not attempt rollback after publication.
- Only after the destination parent fsync succeeds may the receipt be atomically replaced with COMMITTED; fsync the replacement file and parent directory. If this final receipt update fails, report PUBLISHED_UNCONFIRMED (the destination rename itself was fsynced) and retain the prepared receipt for reconciliation.
- Before publication failures must roll back only a keyring entry created by this invocation. Persist NOT_PUBLISHED when possible. If keyring rollback fails, persist/report RECOVERY_REQUIRED and never delete a pre-existing key.
- Keep receipts until an explicit owner cleanup action. No automatic expiry or deletion.

## Inspection contract

Provide a read-only inspection API that accepts an exact receipt path or operation ID plus the expected destination parent. It must validate file type, link count, permissions, schema, operation ID, and destination basename; resolve the destination without following symlinks; and return the receipt state plus whether the destination currently exists and passes a non-mutating profile inventory check.

Inspection must not import/delete keys, repair receipts, activate profiles, overwrite destinations, or retry restore. A PREPARED receipt with an existing destination is conservatively reported as published-but-unconfirmed. A PREPARED receipt with no destination is reported as not currently published, but must retain the receipt for manual review because a crash may have interrupted publication durability.

## Required failure-injection tests

1. Receipt creation or pre-publication fsync fails: destination remains absent; no keyring entry is left behind.
2. Key import fails: destination remains absent; receipt reports NOT_PUBLISHED where durable update is possible.
3. Rename fails: newly created key is rolled back; pre-existing key is untouched.
4. Parent fsync after rename fails: destination and imported key remain; receipt remains inspectable and outcome is PUBLISHED_UNCONFIRMED.
5. Crash-equivalent interruption after rename but before parent fsync: prepared receipt plus destination is reported conservatively as published/unconfirmed.
6. Parent fsync succeeds but committed-receipt replacement fails: destination is retained and inspection reports the prepared/unconfirmed state.
7. Committed receipt replacement and parent fsync succeed: inspection reports COMMITTED.
8. Symlink, hardlink, malformed JSON, unknown state/schema, mismatched operation ID, and destination-name substitution are rejected.
9. Repeated inspection is read-only and idempotent; repeated restore never overwrites or automatically reuses an operation.
10. Cross-platform behavior is explicit: POSIX fsync semantics are tested on supported filesystems; Windows must use a separately verified durable-write strategy or report durability as unconfirmed.

## Scope boundary

This is an implementation contract, not a claim that the current restore code has a durable journal. The current implementation only reports the post-rename fsync failure in a transient BackupError. The helper module is not called by restore_encrypted_backup() yet. The protocol is therefore not end-to-end implemented; do not describe restore recovery as durable or production-ready until orchestration, keyring rollback semantics, cross-platform behavior, and failure-injection tests are integrated and verified.


## Current code status (2026-11-27)

- `ui/desktop/restore_receipts.py` implements owner-only exclusive receipt creation, file and parent-directory fsync on POSIX, atomic terminal-state replacement, strict schema/path/file validation, and read-only inspection. Inspection also checks the restored package directory layout, rejects unsafe filesystem entries, and validates the canonical coding-task state without mutating the destination.
- `tests/test_restore_receipts.py` covers prepared-state inspection, destination-present ambiguity, terminal state, symlink/hardlink rejection, malformed numeric data, operation-ID binding, and invalid inventory.
- `restore_encrypted_backup()` now creates and fsyncs a PREPARED sidecar after staged-tree validation and before importing a portable memory key or publishing the destination. A successful destination-parent fsync is followed by a COMMITTED receipt update. A parent-fsync failure leaves PREPARED, which inspection maps to PUBLISHED_UNCONFIRMED when the destination exists. A failed rename rolls back only a key created by this invocation and records NOT_PUBLISHED; key-import or rollback ambiguity is recorded as RECOVERY_REQUIRED where the receipt can be updated.
- Restore results include the operation ID, receipt path, and COMMITTED state. Publication-uncertain errors include the operation ID and receipt path so the owner can inspect without guessing.
- Receipt directories must be owned by the current user and not group/world writable. Receipt files are exclusive, owner-only, single-link regular files. Non-POSIX receipt creation fails closed because a verified directory-flush primitive is not implemented there.
- The protocol still has a residual ambiguity if receipt persistence itself fails during a keyring rollback error: the best-effort RECOVERY_REQUIRED update may fail, leaving PREPARED. In that case inspect both the receipt and OS keyring; do not retry automatically.
- This is not a global crash-consistency guarantee for arbitrary filesystems or hardware. Durability depends on filesystem/device fsync semantics. End-to-end behavior requires CI verification on supported POSIX targets.
- The implementation intentionally fails closed on non-POSIX platforms because a verified directory-flush strategy is not yet available there.


## Restore orchestration behavior

The restore operation now uses the sidecar lifecycle around the publication boundary. It does not delete a PREPARED receipt after an ambiguous failure, and it never automatically retries or overwrites a published destination. Successful restores return a COMMITTED receipt reference. The read-only inspector does not mutate the receipt or destination.
