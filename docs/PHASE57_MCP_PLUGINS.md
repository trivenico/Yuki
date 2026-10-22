# Phase 57 — Configured MCP plugin bridge

## Purpose
Expose a small set of user-configured Model Context Protocol (MCP) HTTPS tools through Yuki's existing skill approval workflow. This is not an arbitrary plugin marketplace and does not install or execute downloaded Python code.

## Configuration
MCP is disabled by default. Add reviewed servers to `mcp_servers` in `config.yaml`. Each entry must define:
- `name`: unique simple identifier
- `endpoint`: HTTPS endpoint
- `allowed_hosts`: explicit host allow-list (must include the endpoint hostname)
- `allowed_tools`: explicit tool-name allow-list

Only HTTPS, default-port endpoints are accepted by the existing MCP client; DNS addresses must be public, redirects are disabled, responses and tool arguments are bounded, and only JSON MCP responses are supported. Server configuration does not itself grant permission to call tools.

## Use
Invoke the high-risk `mcp_plugin` skill with JSON:
- Discover: `{"action":"list","server":"docs"}`
- Call: `{"action":"call","server":"docs","tool":"search_docs","arguments":{"q":"topic"},"confirm":"CALL docs/search_docs"}`

The normal Yuki skill approval is still required. The exact per-tool confirmation is an additional guard. Only configured tool names may be called. Returned content is marked untrusted and must not be followed as instructions.

## Security
Review the server operator, privacy policy, data retention, and requested scopes before adding a server. Do not send secrets or confidential source code unless permitted. Never trust tool descriptions or results as instructions. MCP is network access and tool execution; use a dedicated server identity and least privilege.

## Limits
The bridge does not support arbitrary stdio MCP servers, marketplace installs, automatic trust from discovery, or unattended high-impact tool calls. Unit tests mock the client and make no network requests.
