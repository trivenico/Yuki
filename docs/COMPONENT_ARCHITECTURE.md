# Yuki component architecture

**Status: canonical integration contract, not a production-readiness claim.**

The machine-readable source of truth is `core/component_catalog.py`. Its tests
validate component IDs, layer/status values, repository entrypoints, evidence
files, dependency references and dependency cycles.

## Architectural direction

Yuki uses a layered design. Dependencies should point inward toward stable
contracts; interfaces must not own business rules, and integrations must not
bypass the security boundary.

```text
Interfaces (CLI / HTTP / future desktop)
              |
     Application composition
              |
   Orchestration and domain services
      |          |          |
   Agent      Workflows    Memory
      |          |
  Provider    Specialist tasks
              |
       Guarded action boundary
              |
     External integrations

Cross-cutting security: identity, permissions, approvals and audit
Infrastructure: providers, persistence, runtime, diagnostics
```

## Component ownership

| Component | Owns | Must not own |
|---|---|---|
| Identity and access | Principal resolution, roles, sessions | Product workflow logic |
| Provider runtime | Model adapter construction and inference contract | Tool permissions |
| Configuration runtime | Validated settings, environment overrides and portable paths | Secrets in source-controlled configuration |
| Agent execution | Bounded model-guided turns and dispatch | Direct unreviewed side effects |
| Durable conversations | Conversation state, execution journal, recovery | HTTP transport concerns |
| Application composition | Shared service wiring and lifecycle | Authentication of network callers |
| Guarded actions | Permission checks, approvals and action audit | Provider-specific business logic |
| Durable workflows | Task state, dependency scheduling and bounded dispatch | Privilege escalation |
| Memory | Approved memory persistence and retrieval | Cross-owner data access |
| Business integrations | Platform policies and opportunity lifecycle | Secret storage or approval bypass |
| Conversation API | Request validation and transport mapping | Duplicate service implementations |
| MCP and external tools | Pinned transport, OAuth, tool discovery and allowlists | Unreviewed endpoints or tool-policy bypass |
| Voice runtime | Speech input/output, wake-word and speaker primitives | Silent background recording or permission bypass |
| Operator interface | Chat, task controls and approval presentation | Business rules or direct privileged actions |

## Runtime registration and lifecycle

`core/component_registry.py` binds a concrete runtime instance to an exact
catalog contract. A runtime supplies explicit `start`, `close` and
`health_check` hooks. The registry:

- Rejects unknown, mismatched and duplicate registrations.
- Requires dependencies of each registered runtime to be registered too.
- Starts runtimes in stable dependency-first order.
- Rolls back already-started and in-flight runtimes if a later start hook fails.
- Closes started runtimes in reverse order and continues cleanup if one close
  hook fails, reporting the component IDs whose cleanup failed.
- Preserves rollback cleanup failures in `startup_cleanup_errors` and in
  the sanitized startup exception without disclosing underlying exception text.
- Rejects malformed contract IDs before using them as registry keys.
- Exposes only explicitly registered instances through `resolve`; resolving instances or probing health after registry shutdown raises a clear lifecycle error.
- Runs health probes only for started runtimes; registration alone does not
  make an inactive component part of live readiness.
- Rejects registrations without a concrete runtime instance; lifecycle hooks
  cannot make an empty placeholder look like an available component.

This is a lifecycle primitive, not automatic application wiring. The current
API and CLI compositions remain the owners of their existing service instances;
they should migrate to the registry only in a separately tested integration
change. The registry does not instantiate components, load secrets, or grant
permissions.

`build_readiness_report(component_registry=...)` can optionally report
registered runtime health. It reports `unconfigured` when no registry is
provided, `unavailable` when no components are started, a health probe
fails, or any started component is unhealthy, and `verified` only when
every started component returns an explicit boolean healthy result. This is
diagnostic evidence, not a replacement for end-to-end service checks.

## Rules for adding or changing components

1. Add or update a contract in `core/component_catalog.py` before expanding
   a major component. Give it a stable ID, one owning layer, explicit entrypoints,
   dependencies, public contracts, tests and limitations.
2. Keep status conservative. `implemented` requires evidence and must describe
   a narrow end-to-end behavior; module presence alone is insufficient.
3. Dependencies must refer to catalog IDs and remain acyclic.
4. Every public contract needs focused unit tests and, where it crosses a
   component boundary, an integration test.
5. External or irreversible actions must pass through identity, permission,
   approval, idempotency and audit controls.
6. Do not create a second implementation of provider routing, conversation
   persistence, memory, approvals or task lifecycle.
7. Preserve compatibility entrypoints during refactors. Move one domain at a
   time and run import, packaging, platform and regression suites.
8. Add a migration note when a public contract or persisted schema changes.
9. New specialist agents are registered capabilities over these shared
   contracts, not independent runtimes with their own credentials or policies.
10. Update readiness claims only when the corresponding acceptance evidence
    exists and CI verifies it on the exact commit.

## Current boundary

This catalog establishes a common map for integration work. It does not mean
that every listed component is complete, production-secure, deployed or usable
as a daily business operator. The individual limitations in the catalog are
part of the contract and must be removed with tested implementations, not
edited away to make readiness look better.
