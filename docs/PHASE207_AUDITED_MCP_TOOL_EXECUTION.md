# Phase 207 — audited owner-confirmed MCP tool execution

## Scope

This phase adds a direct authenticated execution route for one explicitly enabled
MCP tool. It uses the existing host-composed OAuth connection manager, durable
owner/provider/account policy registry, policy revision fencing, and hash-chained
execution audit. It does not add automatic model-directed execution.

## Route

- `POST /v1/integrations/mcp/{provider_id}/{account_id}/tools/{tool_name}/execute`
- JSON body: `{"arguments":{},"expected_revision":2,"confirm":true}`

The request must use one valid owner session. Owner identity and OAuth scopes are
resolved from that session's existing connection; neither is accepted from the
request body. The body is bounded to 64 KB, rejects duplicate JSON keys, and
accepts only the documented fields.

## Execution gates

- The provider must be configured by trusted host startup code.
- The OAuth connection must belong to the authenticated owner and requested
  provider/account.
- The exact tool must have an enabled durable policy for that owner/account.
- The caller must provide the current policy registry revision and literal
  boolean confirmation for this invocation.
- The live policy-bound client rechecks the durable registry revision at the
  authorization boundary, so a concurrent policy change fences stale clients.
- The policy layer uses host-reviewed risk and scope metadata. Remote discovery
  metadata cannot create or lower a tool's risk classification.
- Write and financial tools require the policy callback's fresh per-call owner
  approval; this route binds that callback to the authenticated owner, exact
  tool, trusted policy version/risk, and exact argument object.
- The existing MCP client records execution start and terminal/unknown outcome
  through the durable execution audit before/after dispatch.

## Result handling

Remote results are returned as untrusted content, never as instructions.
Responses are JSON-normalized and capped at 1 MB. The route does not expose
access tokens, authorization headers, endpoint configuration, or credential
material. Errors are intentionally generic where remote or credential details
could otherwise leak.

## Deliberate omissions

- No model may autonomously invoke this route.
- Discovery never enables a tool.
- The route does not add provider onboarding, credential creation, or endpoint
  discovery.
- The caller must already have an authenticated owner session and a connected
  OAuth account.
- No claim is made that a provider supports a particular tool or that the
  external action is reversible.
- CI and integration acceptance remain a later verification gate.
