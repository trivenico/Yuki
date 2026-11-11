# Phase 199 — Audited owner-controlled MCP policy

## Scope

This phase adds explicit owner-authenticated policy enable, disable, and removal
operations for already prepared MCP providers. It does not add a tool execution
route, provider onboarding, or a remote-metadata-to-policy conversion path.

## Routes

- `PUT /v1/integrations/mcp/{provider_id}/{account_id}/policies/{tool_name}`
  - JSON body: `{"enabled":true,"expected_revision":1,"confirm":true}`
  - `enabled` must be a JSON boolean, `expected_revision` an integer, and
    `confirm` the literal JSON boolean `true`.
- `DELETE /v1/integrations/mcp/{provider_id}/{account_id}/policies/{tool_name}`
  - JSON body: `{"expected_revision":1,"confirm":true}`

Both operations require a valid owner session and an OAuth connection belonging
to that owner/provider/account. The owner is derived from the session, never
from request data. Request bodies are bounded, must be JSON objects, reject
duplicate keys, and accept only the exact documented fields.

## Trusted risk boundary

The application must inject an immutable host-reviewed
`MCPTrustedToolDefinition` for each tool that may be enabled. The definition
contains the tool's risk classification and required OAuth scopes. The API
does not accept either field from the owner request, and remote MCP discovery
metadata is never used to construct or lower a risk classification. Missing
trusted definitions fail closed.

An explicit owner-confirmed request is required for each mutation. Enabling a
tool also requires the owner's existing OAuth connection to include every
scope in that host-reviewed definition. Registry revision compare-and-swap
prevents stale clients from overwriting concurrent
policy changes. Policy versions advance on update, and the durable registry
revision advances on every successful set/remove. Existing policy-bound clients
are fenced by the current revision check.

## Audit and failure behavior

Before a mutation is committed, Yuki appends a hash-chained owner-approval
event to the durable audit log. If that append fails, the policy store is not
changed. The event stores a hashed correlation identity, not request arguments,
credentials, or endpoint data. The policy store remains the authoritative
atomic compare-and-swap boundary; a race after audit append can leave an
approval event for a change rejected by the revision check, but cannot apply a
stale policy mutation.

## Deliberate omissions

No MCP execution endpoint is introduced. A policy being enabled does not itself
dispatch a tool. Write and financial tools still require fresh per-invocation
owner approval through the existing policy-bound client. Provider connection
configuration and trusted tool definitions remain host-owned startup inputs.
