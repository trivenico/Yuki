# Phase 207 — private companion memory file

## Finding

The local companion memory store used an atomic temporary-file replacement, but
resolved its configured path with `Path.resolve()` and read through
`Path.read_text()`. It did not reject a final-component symlink or hard-linked
memory file, and it did not explicitly tighten an existing permissive file's
POSIX mode. This store can contain sensitive personal preferences and memories.

## Changes

- Add `core.private_file` primitives for owner-private regular files.
- Create/tighten the memory file to POSIX mode `0600`.
- Reject final-component symlinks and files with multiple hard links where
  supported.
- Read using a no-follow file descriptor, verify the opened inode is regular
  and single-link, and enforce a 16 MiB read bound.
- Keep the existing atomic replacement write path; its temporary file is
  created by `mkstemp` and remains owner-only on POSIX.
- Add tests for file modes, symlink/hard-link rejection, explicit deletion,
  clearing, and refusal to overwrite corrupt memory.

## Limits

- This is not encryption at rest and does not protect against root, malware,
  or another process running as the same OS account.
- Existing parent directories are not chmodded; newly created parent
  directories are requested with mode `0700` only in the shared helper.
- Windows ACL behavior is not established by POSIX mode-bit tests.
- Atomic replacement does not guarantee cross-process lost-update prevention;
  the in-process lock only coordinates threads using the same store object.
- Deleting a memory entry does not erase copies in backups, exports, filesystem
  snapshots, swap, or other independent knowledge indexes.
- Conversation history deletion/export APIs and cross-store user erasure are
  still missing and remain separate work.

## Validation

Await exact-head cross-platform GitHub Actions. No local tests or encryption
claim is made.
