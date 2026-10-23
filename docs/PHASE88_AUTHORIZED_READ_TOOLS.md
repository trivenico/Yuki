# Phase 88 — authorized read-only workspace tools

## Delivered scope

This phase adds an explicit registry with three tools: `workspace.list`, `workspace.read`, and `workspace.search`. Every call requires a trusted principal, the `workspace:read` permission, bounded JSON arguments, and successful audit preflight. A denied or unknown tool is not dispatched.

## Security controls

- Workspace paths are resolved under one configured root; absolute paths and parent traversal are rejected.
- Symlinks are excluded from listing/search and paths resolving outside the workspace are rejected.
- Secret-like names and sensitive directories (`.env*`, credentials, keys, `.git`, virtual environments, caches, and common dependency directories) are excluded.
- Reads are UTF-8 text only and limited to 256 KiB per file. Search is bounded by query length, file count, scanned bytes, and result count.
- Returned file/page text is explicitly marked untrusted. Tool output must not be treated as policy or trusted instructions by a downstream model.
- Audit callback failure denies execution. Audit records contain actor, tool, outcome, and stable code, not file contents or prompts.
- No write, shell, process execution, browser navigation, network access, or Git mutation tools are registered.

## Integration contract

The application must resolve the authenticated identity server-side and construct `ToolPrincipal` only from that trusted result. Never deserialize a client-provided principal or permission set. Configure `principal_is_trusted` to validate the principal against the application's trusted session/identity source. The audit callback must write to the application's durable audit sink.

This module is not a replacement for the Phase 81 application authentication integration; it is a reusable enforcement boundary. The tools are not automatically connected to the provider-backed specialist worker in this phase. Wire them through an explicitly authorized adapter in a later phase.

## Validation

Phase 88 CI runs the dedicated tests and dependency consistency checks on Python 3.11 for Ubuntu, Windows, and macOS. Required checks must pass before merge.
