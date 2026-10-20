# Phase 6 production hardening

## Scope

This branch hardens two concrete risk surfaces in the existing repository:

- MCP HTTPS transport: pins DNS results at connect time, rejects mixed public/private DNS responses, disables environment proxies and redirects, validates JSON-RPC response IDs, serializes session/request state, bounds input/output, and fails closed unless approval returns the literal boolean `True`.
- Learning history: stores state beneath the configured Yuki data root, redacts common token-like strings, bounds record count and fields, uses private POSIX permissions where supported, writes to a same-directory temporary file, fsyncs the file, atomically replaces the destination, and attempts to fsync the directory.

## Important limitations

- DNS pinning reduces rebinding risk, but production deployments should still apply network egress controls. No user-space HTTP client can replace host/container network policy.
- The MCP client intentionally supports JSON responses only; it is not a complete MCP SDK implementation and does not support SSE-only responses or stdio servers.
- The learning JSON file remains a single-process convenience store. Atomic replacement prevents torn writes but does not make concurrent read-modify-write updates serializable. Use SQLite transactions for multi-process writers.
- Python plugins execute with Yuki's process privileges. AST scanning is not a sandbox; keep discovery disabled by default and use OS/container isolation for untrusted code.
- These changes do not imply that the whole application is production-ready.

## Verification

Run:

```sh
python -m unittest discover -s tests -v
python -m compileall -q .
```

The new tests mock DNS and do not require real network access. Review CI results on the pull request before merging.
