# Java enterprise MCP integration

Yuki's Java 17 adapter is located in `integrations/java/mcp-client`. It
supports an enterprise JVM application that already has an authenticated Yuki
session and needs to inspect available MCP tool metadata.

The adapter calls only the owner-authenticated
`GET /v1/integrations/mcp/{provider_id}/{account_id}/tools` endpoint. It
validates the API host, requires TLS outside loopback development, disables
redirects, applies deadlines, bounds response bodies, and returns typed
metadata. It rejects any response that indicates execution is enabled.

Java is deliberately not another OAuth provider, credential vault, policy
engine, or tool executor. Yuki's Python API validates the session and resolves
the owner from it; durable policy and audit stay in Python. Remote tool
descriptions and schemas remain untrusted data.

The module is an additive integration and does not change the existing core
layout. Build and test instructions are in
`integrations/java/mcp-client/README.md`.
