# Yuki Java MCP management client

Java 17 read-only adapter for Yuki's authenticated MCP management API.

The Python service remains authoritative for OAuth discovery and PKCE,
credential storage, session validation, owner/account binding, scopes, durable
tool policies, approvals and audit. Java receives an existing Yuki session
token from the embedding application and forwards it only to the configured
Yuki API over HTTPS (HTTP is permitted for loopback development only).

The client exposes only `discoverTools(providerId, accountId)`. It cannot
invoke MCP tools, create connections, change policies, or persist credentials.
Remote tool metadata remains untrusted and all returned execution flags must
remain false. Responses are bounded to 1 MiB, redirects are disabled, and
connection/request deadlines are enforced.

## Build and test

Requires JDK 17 and Gradle 8.10 or newer:

```sh
gradle test
```

This module is independently buildable and is not yet wired into Yuki's
production runtime. Integrate it only through the authenticated Python API;
never give the Java process direct OAuth vault or policy-store access.
