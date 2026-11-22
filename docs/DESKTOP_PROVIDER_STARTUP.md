# Desktop provider-independent startup

The desktop process now composes a `LazyDesktopProvider` rather than constructing
the configured provider during application startup.

## Behavior

- Authentication, local SQLite services, task coordination, and the durable
  conversation store can initialize without provider credentials or a running
  local model server.
- The provider factory runs on first model inference, not during desktop startup.
- Initialization is serialized across concurrent callers. A failed
  initialization is sanitized and cached for the lifetime of the desktop
  process; Yuki does not silently retry or switch providers.
- The adapter preserves both desktop chat contracts: structured
  `generate_chat(messages, tools, tool_choice)` and the legacy
  `generate_chat(system, messages)` interface. The legacy path is translated
  into a structured request with an empty tool list and `tool_choice="none"`.
- The desktop lifecycle owns the adapter and closes an initialized provider
  once. Closing before first use does not instantiate a provider.
- Streaming is not advertised by the lazy adapter. This avoids probing or
  initializing an optional provider during startup; streaming can be added
  later through an explicit lazy capability contract.

This change does not add a mock fallback, grant tools, alter account ownership,
or change authentication. Model-dependent chat still requires the configured
provider to be available.
