# Phase 195 — Trusted Gmail runtime composition

## What this adds

`core/google_gmail_runtime.py` provides one host-side composition factory for the Gmail send path. It joins:

- The existing host-authored tool handlers and capability catalog.
- The approval-gated `gmail.send_email` handler and its strict model schema.
- The shared guarded runtime with mandatory trusted action context, durable action journal, and single-use approval store.
- The Gmail read-only reconciliation adapter under the exact same `gmail.send_email` identity.
- The owner-scoped pending proposal store used by the reconciliation service.

The returned `GmailToolRuntimeBundle` contains the guarded runtime, complete model tool definitions, and reconciliation service.

## Host integration

The deployment host must first construct the Google OAuth provider, owner-scoped connection registry, encrypted credential vault, refresh service, pinned HTTPS transport, and the two Gmail clients. It then calls `build_gmail_tool_runtime_bundle(...)` with those trusted clients and its existing tools/policy. The host passes `bundle.guarded_runtime` to `StructuredToolProtocol`, uses `bundle.tool_definitions` for the model-facing tool list, and supplies `bundle.reconciliation_service` to `build_conversation_runtime`.

The host still owns authenticated identity, UI confirmation, policy decisions, persistence paths, OAuth callback routing, and lifecycle shutdown. This factory does not read environment variables, create OAuth secrets, connect an account, or send email during startup.

## Safety properties

- Gmail cannot be invoked directly; the guarded runtime must provide the exact host-created action context and authenticated principal.
- A Gmail send proposal cannot execute before its durable approval is granted.
- The existing policy must explicitly authorize `gmail.send_email`.
- The durable journal fences duplicate dispatch and records uncertain outcomes.
- Reconciliation only performs a read-only provider lookup; an absent Gmail search result remains unknown and never permits a blind resend.
- Existing tools must have exactly one host-authored capability and one model schema each. Duplicate Gmail registration is rejected.
- The factory rejects missing stores, mismatched registrations, and malformed trusted definitions.

## Not yet a live deployment

This phase completes the composition seam, not the external Google setup. A real deployment still needs operator-supplied Google OAuth client credentials and trusted redirect-host configuration, explicit OS-key-store initialization, production database locations/backups, a UI that presents and records approval decisions, and a deployment-level egress firewall. No real Google account was connected or used by these tests.
