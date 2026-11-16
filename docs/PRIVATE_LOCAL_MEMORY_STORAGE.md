# Private local memory storage

## Change

The JSON-backed local memory store now creates new memory files with owner-only
POSIX permissions, restricts existing regular single-link files to owner-only
permissions, rejects symlinks where the platform supports no-follow opens, and
rejects hard-linked or non-regular files. New files are initialized to an empty
JSON list before reads. Saves use a same-directory temporary file, flush and
fsync its contents, atomically replace the target, and fsync the parent
directory on POSIX.

The store validates persisted entry structure and text bounds. Corrupt existing
files fail closed; they are not silently reset or overwritten.

## Boundaries

- This is filesystem permission and path-hardening, not encryption.
- It does not protect against another process running as the same OS user.
- Windows ACLs remain the responsibility of the user profile and deployment.
- Memory remains opt-in at the call site; no network, embeddings, or model
  training are introduced.
- This JSON store is distinct from the owner-scoped SQLite conversation
  transcript store.

## Validation

Focused tests cover initialization, permissions, add/list/delete/clear,
symlink and hard-link rejection, corrupt-file preservation, and input bounds.
GitHub Actions must validate the exact PR head; no local test execution is
claimed.
