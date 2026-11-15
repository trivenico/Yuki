# Phase 194 — Approval-gated Gmail send tool

## Implementation

- Adds the `gmail.send_email` model-facing tool schema with a closed argument
  object and required connected-account, recipient, subject, and body fields.
- Adds a host-authored capability classification: side-effecting, explicit
  user confirmation required, no assumed provider idempotency, reconciliation
  supported.
- Adds a context-aware handler. Calling it directly is denied; dispatch must
  come through `GuardedToolRuntime` with a host-created `ToolActionContext`
  and authenticated principal.
- Extends guarded dispatch to pass that trusted context only to handlers that
  explicitly implement `invoke_action`. Existing callable tool handlers keep
  their original one-argument contract.
- Binds the exact proposal action ID to the deterministic RFC 822 Message-ID,
  and the authenticated principal to the owner-scoped credential lookup.
- Keeps account selection explicit and includes it in the approved arguments.
  The connected-account credential resolver remains the authority for whether
  that principal owns the requested account and has the required send scope.

## Safety behavior

The proposal/approval executor remains the only route to an approved send.
The Gmail client makes one provider POST and never retries ambiguous outcomes.
The reconciliation adapter performs read-only lookup of the action-bound
Message-ID; absence remains unknown and never authorizes a resend.

## Integration status

The handler and capability metadata are ready to be registered by the host
tool composition root. This phase does not automatically publish the tool to
all users, configure Google OAuth credentials, build the approval UI, or claim
a production Gmail integration. The host still needs to compose the Gmail
client, register its handler/schema/capability, register the reconciliation
adapter for the same provider/action, and verify the full lifecycle in an
integration environment.
