# WI-5T — Production policy-revision composition fence

## Finding

The typed authorization layer already compares each decision's policy revision with the revision requested by the caller, and the production API runtime already enforced exact AuthorizationGateway object identity. However, the strict owner-scoped API composition check only required the guarded-tool and direct-provider adapters to have a non-empty policy revision (the provider) or did not inspect the guarded-tool revision at all. It did not require either adapter's expected revision to equal the ProductionSecurityContext revision.

A host could therefore accidentally compose the correct shared gateway and principal with an old-but-valid policy revision. If the policy implementation continued serving that old revision, the adapters could obtain allows against stale policy while the production context advertised a newer revision.

## Changes

- Expose GuardedToolRuntime's configured expected policy revision as a read-only property for host composition validation.
- In strict API runtime composition, require the guarded-tool runtime revision to exactly equal ProductionSecurityContext.policy_revision.
- Require the direct provider adapter revision to exactly equal the same production revision (not merely be non-empty).
- Add regression tests that build a strict API runtime with the correct gateway/principal but stale tool-policy or provider-policy revisions and assert the owner loop factory rejects both configurations.

## Security behavior

The runtime rejects a mismatched owner loop before it is used to execute a conversation. At invocation time, the gateway still compares the returned decision revision to the expected revision in each AuthorizationRequest and fails closed on stale decisions. Gateway identity, principal identity, and policy revision are separate composition invariants; all three must match.

## Limits

This change fences strict API owner-loop composition. It does not make every legacy CLI, locally composed worker, or independently deployed process strict automatically; those entry points still require host bootstrap and migration checks. It also does not provide a distributed policy push protocol. A context pins a revision, and a policy update causes old contexts to fail closed until they are rebuilt with the new revision.

Tests were added but not executed in this environment.
