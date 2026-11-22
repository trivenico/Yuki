# API provider-independent startup

The web entry point constructs Yuki's authenticated application without eagerly
constructing the configured language-model provider.

## Startup behavior

- Authentication, conversation persistence, task/runtime wiring, and the
  provider catalog are initialized independently of model credentials.
- A configured provider is constructed only when the first chat inference or
  streaming request reaches the provider adapter.
- Provider initialization is serialized across concurrent first requests.
- A provider initialization failure is cached for that runtime and exposed as
  the stable error `configured_provider_unavailable`; provider details and
  credentials are not copied into the error message.
- No mock provider or implicit cloud fallback is used.
- Persisted provider selection is restored as configuration metadata without
  constructing a provider during startup. The administrator's explicit
  provider-selection action still validates readiness and constructs the
  selected provider before committing the change.
- The API runtime closes the model adapter during ASGI shutdown. Closing an
  unused lazy provider does not construct it.

## Boundaries

This change does not alter authentication, account ownership, tool
authorization, conversation storage, or the model/tool execution protocol.
Provider output remains untrusted and tool execution remains inside Yuki's
existing guarded runtime.

Provider catalog readiness is configuration metadata, not proof that a
provider can currently serve inference. A successful first inference is still
the only evidence that the configured model endpoint is working.
