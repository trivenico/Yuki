# Mobile-first access to Yuki when no PC is available

## Decision

Yuki must remain usable by its owner without requiring a personally owned laptop or desktop. Keep the native desktop client as a later/parallel interface, but make a secure mobile browser client the first practical access surface for a cloud-hosted Yuki runtime.

This document is an architecture decision and delivery plan, not a claim that a mobile app, public API, or cloud deployment already exists.

## Target architecture

```text
Phone browser / installable PWA
          |
      HTTPS only
          |
  Owner-authenticated API
          |
  Application service boundary
          |
  Yuki runtime on Oracle Cloud
    |          |          |
 providers   encrypted   approved
             memory      task worker
```

The mobile UI and desktop UI must call the same application-service contracts. Do not put provider routing, memory policy, approval rules, or task execution inside the browser.

## First usable mobile slice

1. Owner-only sign-in and session revocation.
2. Persistent chat: create conversation, send message, stream or poll response, list history.
3. Explicit provider status and safe error messages.
4. Read-only memory inspection and user-controlled memory deletion/retention controls.
5. Task and specialist status; no destructive actions or tool approvals until the exact action, scope, and consequences can be reviewed on a small screen.
6. Responsive layout and installable PWA shell; offline mode may show cached UI but must never imply a queued action succeeded.

## Security requirements

- Bind the service to a private interface during development. Production ingress is HTTPS behind a maintained reverse proxy or managed edge.
- Never expose SQLite files, the desktop profile directory, OS keyring, shell, Docker socket, or raw provider credentials to the browser or public network.
- Use owner-only authentication, short-lived server-side sessions, secure HttpOnly/SameSite cookies, CSRF protection for cookie-authenticated mutations, rate limits, login throttling, and explicit logout/revocation.
- Require TLS, strict origin/CORS allowlisting, request-size limits, schema validation, security headers, and redacted audit events.
- Keep API keys and model credentials in server-side secret storage. The browser must never receive them.
- Enforce authorization and approval in the application service on every request; hiding a button is not authorization.
- Separate chat/read-only access from tool execution. Require a fresh, scoped approval for sensitive actions and re-check it at execution time.
- Use a private network or authenticated tunnel for owner administration. Do not expose an unauthenticated admin panel.
- Back up encrypted data, test restore, and document recovery before relying on the cloud instance for personal data.

## Cloud deployment constraints

- Oracle Cloud is the intended always-on host, but deployment is not complete until a hardened instance, firewall/egress policy, service supervision, encrypted backups, monitoring, patching, and restore drills are verified.
- Do not treat Docker alone as a security boundary.
- Keep the first hosted version single-owner and single-tenant. Avoid building multi-user SaaS complexity for a personal assistant.
- Keep local desktop/portable storage distinct from cloud storage. Cross-device synchronization must use an explicit authenticated protocol, not shared filesystem paths.
- Do not make Google OAuth mandatory. Provider/account integrations (Google, GitHub, LinkedIn where supported) remain optional, individually authorized connectors.

## Training without a personal computer

A phone is sufficient to manage a cloud notebook session and inspect experiment reports, but it is not a good environment for running model training locally. Use Kaggle GPU notebooks or another managed GPU environment for experiments; keep datasets, checkpoints, and secrets under controlled storage. Do not put cloud API keys or private datasets in public notebooks or repository commits.

The first training milestone should be a reproducible small fine-tuning experiment on a suitable open model, not training a foundation model from scratch. Require a dataset license review, held-out evaluation, checkpoint provenance, and a rollback path before any model is offered to the live assistant.

## Delivery order

1. Complete the shared core conversation/authentication/runtime and current cross-platform CI.
2. Add a minimal authenticated API over the existing application-service boundary.
3. Add a responsive PWA for owner chat and status only.
4. Deploy privately to Oracle Cloud; validate firewall, TLS, secrets, backup/restore, and recovery.
5. Enable carefully scoped read-only connectors, then approved actions.
6. Build the reproducible Kaggle training/evaluation pipeline in parallel after the core is stable.

## Acceptance criteria for first mobile use

- Owner can sign in from a phone browser without owning a PC.
- Chat history survives server restart and is isolated to the owner.
- Revoked sessions cannot read history or submit new requests.
- Browser traffic contains no provider API keys or desktop credentials.
- Unapproved tools cannot execute through direct API requests.
- A backup can be restored and the service restarted without losing the owner's conversations.
- The service can be stopped safely and restarted after a failed provider request.
