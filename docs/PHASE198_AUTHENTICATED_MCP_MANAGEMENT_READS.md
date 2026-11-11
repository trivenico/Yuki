# Phase 198 — Authenticated MCP management reads

## Scope

This phase adds owner-authenticated read endpoints for MCP tool discovery and
policy inspection. It does not add policy mutation or tool execution endpoints.

## Routes

- `GET /v1/integrations/mcp/{provider_id}/{account_id}/tools`
- `GET /v1/integrations/mcp/{provider_id}/{account_id}/policies`

Both routes require one valid bearer session header. Owner identity is resolved
from the session and is never accepted from the URL or request body. The
requested provider/account pair must already exist in the OAuth connection
registry for that owner.

## Security properties

- Provider managers, endpoint URLs, host allow-lists, and required scopes come
  only from trusted application startup configuration.
- Discovery uses the manager's deny-all client and returns inert metadata only.
- Every discovered tool is marked `read_only: false` and
  `execution_enabled: false`; remote metadata never grants permissions.
- Policy reads return the durable owner/provider/account snapshot and revision.
- Credential values, authorization headers, endpoint URLs, and raw token data
  are never included in responses.
- Responses are bounded by the existing discovery schema and metadata limits,
  JSON encoded, and marked `Cache-Control: no-store`.

## Deliberate omissions

No policy create/update/delete routes or execution route are exposed in this
phase. Policy changes need their own audited owner-confirmation workflow and
revision compare-and-swap handling. Execution remains unavailable through the
management API. Provider integrations must be separately prepared by trusted
host configuration and explicitly connected by the owner.
