# Phase 193 — owner-scoped inert MCP tool discovery

## Delivered

- Permit an empty MCP execution allow-list as an explicit deny-all policy.
- Add a discovery service that initializes an already host-bound MCP client and requests `tools/list`.
- Bind each snapshot to the authenticated owner, trusted provider and configured provider account supplied by host code.
- Bound the remote tool count, tool names, descriptions, schema depth, schema item count and serialized schema size.
- Publish remote descriptions and schemas as untrusted metadata.
- Mark every discovered tool as `read_only=False` and `execution_enabled=False`; server descriptions never establish safety or grant permissions.

## Security boundaries

- The OAuth connection manager still requires a callable per-call approver. Empty `allowed_tools` means no tool can pass the MCP client's allow-list check.
- Discovery does not call `tools/call`, persist a tool catalog, enable a tool, or infer read-only behavior.
- The host must create the client with owner identity from the authenticated session and provider/account/endpoint/host policy from trusted configuration.
- Server-provided metadata remains untrusted even after prompt-injection scanning. A clean scan is not an authorization decision.
- Tool execution requires a separate trusted review and explicit allow-list plus per-call approval policy.

## Validation

Tests cover identity binding, deny-all execution posture, untrusted descriptions, duplicate/invalid names, bounded schemas and sanitized remote errors. CI is authoritative; no local test execution is claimed.
