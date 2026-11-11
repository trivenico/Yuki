# Phase 197 — host-composed MCP application runtime

## Delivered

- Add an application service that composes trusted OAuth MCP connection managers, immutable durable policy snapshots, revision fencing, and shared metadata-only audit.
- Require an exact provider manager/configuration match at startup; reject unconfigured providers and mismatched manager identities.
- Keep endpoint and host allow-lists in host-owned immutable configuration, not model or request input.
- Build a fresh owner/provider/account policy snapshot for each client.
- Re-check the durable policy revision for every tool approval so revocation invalidates already-created clients.
- Record policy decisions and per-invocation execution lifecycle through the shared hash-chained audit log.
- Bind execution audit identity to the exact tool using a stable digest of provider, account and tool identifiers.
- Require the configured provider scopes before constructing a client.

## Security boundaries

The service does not bootstrap providers, discover endpoints, authorize OAuth, persist credentials, enable a tool policy, or infer risk from remote metadata. Host startup code must supply already-prepared OAuth managers and endpoint allow-lists. Owner identity must come from the authenticated application session. A missing policy, stale revision, audit failure, missing scope or owner-approval failure denies execution.

## Scope

This is a composition service, not yet an HTTP API or default application runtime integration. No provider is connected and no external action is enabled by this phase.

## Validation

CI is authoritative; no local test execution is claimed.
