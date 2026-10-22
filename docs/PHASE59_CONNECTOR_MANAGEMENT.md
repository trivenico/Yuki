# Phase 59 — Connector catalog and diagnostics

Yuki's connector-management skill exposes two operations:

- `{"action":"list"}`: summarizes the explicitly configured MCP connectors locally. It makes no network requests and does not reveal credentials.
- `{"action":"check","server":"docs"}`: performs MCP initialization and tool discovery for one configured HTTPS endpoint. It does not invoke any tool or mutate configuration.

Connector configuration lives in `mcp_servers` in `config.yaml`. Each entry must specify a unique name, HTTPS endpoint, exact host allow-list, and allowed tool names. The configuration defaults to an empty list. A connector is not enabled merely because a package exists; it must be explicitly configured.

## Trust and security
- Discovery is not execution permission. Only configured tool names are executable by the separate MCP skill, and each call requires exact confirmation plus Yuki's normal approval gate.
- Health checks contact the configured remote endpoint and therefore may disclose connection metadata such as source IP, time and requested endpoint. Run them only for a service you trust.
- The current connector schema does not support credentials or OAuth setup. Do not put secrets in endpoint URLs or YAML. Add authentication only through a reviewed secret-provider integration.
- No arbitrary Python package or marketplace plugin is installed by this skill. Python plugins execute with Yuki's OS permissions and remain disabled by default.
- To disable a connector, remove its entry from `mcp_servers` and restart Yuki. To disconnect remote credentials, use the provider's own revocation process; this schema does not store credentials.
- This is a diagnostics/catalog feature, not a guarantee that every MCP server is compatible. The current HTTP client supports JSON MCP responses and does not support SSE-only responses.

Tests mock network interactions and make no real requests.
