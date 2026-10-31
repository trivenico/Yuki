# Phase 103 — persistent agent sessions

## Goal

Persist conversation sessions and messages across CLI invocations, scoped to a workspace. This is a storage/recovery foundation, not an authorization or execution system.

## Implemented

- SQLite-backed conversation and ordered-message tables.
- Mandatory workspace scoping for session reads, writes, listings, and archival.
- Stable message sequence cursors for bounded history retrieval.
- Idempotent message appends with conflict detection.
- Archived sessions are read-only and hidden from the default active-session listing.
- Bounded titles, content, metadata, pagination and JSON serialization.
- CLI lifecycle commands: list, create, show and archive.
- Persistent CLI run mode that creates a new session or resumes an existing one and includes bounded prior messages in the next provider request.
- Session ID is carried through the existing NDJSON event stream.
- Experimental capability contract and regression tests.

## Usage

Create a persistent session and run the first turn:

```bash
python yuki_cli.py run "Let's plan Yuki" --format json --workspace project-yuki --new-session
```

The `start` event contains the persistent session ID. Resume it in another process:

```bash
python yuki_cli.py run "Continue the plan" --format json --workspace project-yuki --session SESSION_ID
```

Inspect and manage sessions:

```bash
python yuki_cli.py sessions list --workspace project-yuki
python yuki_cli.py sessions show --workspace project-yuki SESSION_ID
python yuki_cli.py sessions archive --workspace project-yuki SESSION_ID
```

The database is stored at `$YUKI_HOME/conversations.sqlite3`, or `~/.yuki/conversations.sqlite3` when `YUKI_HOME` is unset.

## Security boundaries

- Workspace ID is supplied by the local CLI caller in this phase. It is a data partition key, not proof of authenticated identity. Hosted deployments must derive workspace identity from trusted authentication middleware, never an arbitrary client field.
- Session IDs do not grant access. Every read/write also checks workspace ID.
- Conversation records do not contain or grant tool permissions, approvals, credentials or authorization decisions.
- The simple `run` path still has tool execution disabled and uses the configured provider's single-turn generate API.
- Persisted content is untrusted context. It must not override system policy or tool authorization.
- Database encryption-at-rest, retention/deletion policy, multi-device synchronization, concurrent session writer UX and desktop integration are not included.
- Failed provider requests leave the user message in the conversation, preserving the request for later inspection/retry.
- The context builder uses a bounded recent-message window; it is not yet token-aware and does not implement summarization.

## Verification

```bash
python -m unittest tests.test_conversation_store -v
python -m unittest tests.test_yuki_cli -v
python -m unittest tests.test_capability_manifest -v
python -m unittest discover -s tests -v
```

Keep experimental until exact-head Linux and Windows checks pass. Phase 102 is the parent; merge parent phases in dependency order before this PR.
